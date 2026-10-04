import * as fs from 'fs';
import * as path from 'path';
import { spawnSync } from 'child_process';
import { commands, ExtensionContext, OutputChannel, workspace, window } from 'vscode';
import {
  LanguageClient,
  LanguageClientOptions,
  ServerOptions,
} from 'vscode-languageclient/node';

let client: LanguageClient | undefined;
let output: OutputChannel;
let startup: Promise<void> = Promise.resolve();

/** Max parent directories to walk up from a workspace folder when looking for .venv. */
const VENV_SEARCH_DEPTH = 6;

/** Check that this interpreter can import the actual language server. */
function canRunServer(cmd: string, serverModule: string): boolean {
  try {
    const probe = `import sys; sys.path.insert(0, ${JSON.stringify(path.dirname(serverModule))}); import server`;
    const result = spawnSync(cmd, ['-c', probe],
      { stdio: 'pipe', timeout: 5000 });
    return !result.error && result.status === 0;
  } catch {
    return false;
  }
}

/** Look for a `.venv` starting at *startDir* and walking up its ancestors. */
function findVenvUpwards(startDir: string): string | undefined {
  let dir = startDir;
  for (let i = 0; i < VENV_SEARCH_DEPTH; i++) {
    const winVenv = path.join(dir, '.venv', 'Scripts', 'python.exe');
    if (fs.existsSync(winVenv)) {
      return winVenv;
    }
    const unixVenv = path.join(dir, '.venv', 'bin', 'python');
    if (fs.existsSync(unixVenv)) {
      return unixVenv;
    }
    const parent = path.dirname(dir);
    if (parent === dir) {
      break;
    }
    dir = parent;
  }
  return undefined;
}

/**
 * Resolve the Python interpreter path.
 * Priority: explicit setting > VS Code Python setting > workspace .venv >
 * extension checkout .venv. Every candidate must load the server successfully.
 */
function resolvePythonPath(context: ExtensionContext, serverModule: string): string | undefined {
  const candidates: string[] = [];
  // 1. Explicit extension setting
  const configured = workspace.getConfiguration('lvjiangWf').get<string>('pythonPath')?.trim();
  if (configured) {
    candidates.push(configured);
  }

  // 2. VS Code Python extension setting
  const vscodePython = workspace.getConfiguration('python').get<string>('defaultInterpreterPath')?.trim();
  if (vscodePython && vscodePython !== 'python') {
    candidates.push(vscodePython);
  }

  // 3. Auto-detect .venv, walking up from each workspace folder (handles opening a subfolder)
  const folders = workspace.workspaceFolders;
  if (folders) {
    for (const folder of folders) {
      const found = findVenvUpwards(folder.uri.fsPath);
      if (found) { candidates.push(found); }
    }
  }
  const extensionVenv = findVenvUpwards(fs.realpathSync(context.extensionPath));
  if (extensionVenv) { candidates.push(extensionVenv); }
  for (const candidate of [...new Set(candidates)]) {
    if (canRunServer(candidate, serverModule)) { return candidate; }
    output.appendLine(`Skipping Python without WF server dependencies: ${candidate}`);
  }
  output.appendLine('WF language server disabled: no suitable Python environment. Syntax highlighting and snippets remain available.');
  return undefined;
}

async function startServer(context: ExtensionContext): Promise<void> {
  const serverModule = context.asAbsolutePath(path.join('server', '__main__.py'));
  const pythonPath = resolvePythonPath(context, serverModule);
  if (!pythonPath) { return; }

  const serverOptions: ServerOptions = {
    command: pythonPath,
    args: [serverModule],
    options: {
      env: {
        ...process.env,
        LVJIANG_WF_LAYOUT_KEY: workspace.getConfiguration('lvjiangWf').get<string>('layoutKey') || '',
      },
    },
  };

  const clientOptions: LanguageClientOptions = {
    documentSelector: [{ scheme: 'file', language: 'wf' }],
  };

  const nextClient = new LanguageClient(
    'lvjiangWfServer',
    'LvJiang WF Server',
    serverOptions,
    clientOptions,
  );

  try {
    await nextClient.start();
    client = nextClient;
    output.appendLine(`WF language server started with ${pythonPath}`);
  } catch (error) {
    output.appendLine(`WF language server failed: ${String(error)}`);
  }
}

export function activate(context: ExtensionContext) {
  output = window.createOutputChannel('LvJiang WF');
  context.subscriptions.push(output);
  context.subscriptions.push(commands.registerCommand('lvjiangWf.restartServer', async () => {
    startup = startup.then(async () => {
      if (client) { await client.stop(); client = undefined; }
      await startServer(context);
    });
    await startup;
  }));
  context.subscriptions.push(workspace.onDidChangeConfiguration(event => {
    if (event.affectsConfiguration('lvjiangWf.pythonPath') ||
        event.affectsConfiguration('lvjiangWf.layoutKey') ||
        event.affectsConfiguration('python.defaultInterpreterPath')) {
      void commands.executeCommand('lvjiangWf.restartServer');
    }
  }));
  void commands.executeCommand('lvjiangWf.restartServer');
}

export function deactivate(): Thenable<void> {
  return startup.then(async () => {
    if (client) { await client.stop(); client = undefined; }
  });
}
