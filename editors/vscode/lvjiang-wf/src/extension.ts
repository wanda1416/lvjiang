import * as fs from 'fs';
import * as path from 'path';
import { spawnSync } from 'child_process';
import { ExtensionContext, workspace, window } from 'vscode';
import {
  LanguageClient,
  LanguageClientOptions,
  ServerOptions,
} from 'vscode-languageclient/node';

let client: LanguageClient;

/** Max parent directories to walk up from a workspace folder when looking for .venv. */
const VENV_SEARCH_DEPTH = 6;

/** Check whether *cmd* resolves to a runnable executable on PATH. */
function commandExists(cmd: string): boolean {
  try {
    const result = spawnSync(cmd, ['--version'], { stdio: 'ignore' });
    return !result.error;
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
 * Priority: explicit setting > VS Code Python setting > auto-detect .venv (walking up
 * from each workspace folder) > system python3/python.
 */
function resolvePythonPath(): string {
  // 1. Explicit extension setting
  const configured = workspace.getConfiguration('lvjiangWf').get<string>('pythonPath')?.trim();
  if (configured) {
    window.showInformationMessage(`LvJiang WF: Using configured Python path: ${configured}`);
    return configured;
  }

  // 2. VS Code Python extension setting
  const vscodePython = workspace.getConfiguration('python').get<string>('defaultInterpreterPath')?.trim();
  if (vscodePython && vscodePython !== 'python') {
    window.showInformationMessage(`LvJiang WF: Using VS Code Python path: ${vscodePython}`);
    return vscodePython;
  }

  // 3. Auto-detect .venv, walking up from each workspace folder (handles opening a subfolder)
  const folders = workspace.workspaceFolders;
  if (folders) {
    for (const folder of folders) {
      const found = findVenvUpwards(folder.uri.fsPath);
      if (found) {
        window.showInformationMessage(`LvJiang WF: Auto-detected .venv at: ${found}`);
        return found;
      }
    }
  }

  // 4. Fall back to a system interpreter. Prefer python3 on POSIX, since plain
  // `python` frequently doesn't exist there (only on Windows is it the norm).
  const candidates = process.platform === 'win32' ? ['python', 'py'] : ['python3', 'python'];
  for (const candidate of candidates) {
    if (commandExists(candidate)) {
      window.showWarningMessage(
        `LvJiang WF: No .venv found, falling back to system "${candidate}". ` +
        `The language server needs "pygls" installed there (pip install -e ".[dev]"), ` +
        `or set "lvjiangWf.pythonPath" to your project's .venv interpreter. ` +
        `Folders: ${folders?.map(f => f.uri.fsPath).join(', ') || 'none'}`,
      );
      return candidate;
    }
  }

  window.showErrorMessage(
    `LvJiang WF: No Python interpreter found (tried ${candidates.join(', ')}). ` +
    `Set "lvjiangWf.pythonPath" in settings to a Python interpreter with "lvjiang" and "pygls" installed.`,
  );
  return candidates[0];
}

export function activate(context: ExtensionContext) {
  const serverModule = context.asAbsolutePath(path.join('server', '__main__.py'));
  const pythonPath = resolvePythonPath();

  const serverOptions: ServerOptions = {
    command: pythonPath,
    args: [serverModule],
  };

  const clientOptions: LanguageClientOptions = {
    documentSelector: [{ scheme: 'file', language: 'wf' }],
  };

  client = new LanguageClient(
    'lvjiangWfServer',
    'LvJiang WF Server',
    serverOptions,
    clientOptions,
  );

  client.start();
}

export function deactivate(): Thenable<void> | undefined {
  if (!client) {
    return undefined;
  }
  return client.stop();
}
