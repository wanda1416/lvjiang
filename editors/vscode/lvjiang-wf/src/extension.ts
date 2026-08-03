import * as fs from 'fs';
import * as path from 'path';
import { ExtensionContext, workspace } from 'vscode';
import {
  LanguageClient,
  LanguageClientOptions,
  ServerOptions,
} from 'vscode-languageclient/node';

let client: LanguageClient;

/**
 * Resolve the Python interpreter path.
 * Priority: explicit setting > VS Code Python setting > auto-detect .venv > 'python'
 */
function resolvePythonPath(): string {
  // 1. Explicit extension setting
  const configured = workspace.getConfiguration('lvjiangWf').get<string>('pythonPath');
  if (configured) return configured;

  // 2. VS Code Python extension setting
  const vscodePython = workspace.getConfiguration('python').get<string>('defaultInterpreterPath');
  if (vscodePython && vscodePython !== 'python') return vscodePython;

  // 3. Auto-detect .venv in workspace folders
  const folders = workspace.workspaceFolders;
  if (folders) {
    for (const folder of folders) {
      const winVenv = path.join(folder.uri.fsPath, '.venv', 'Scripts', 'python.exe');
      if (fs.existsSync(winVenv)) return winVenv;
      const unixVenv = path.join(folder.uri.fsPath, '.venv', 'bin', 'python');
      if (fs.existsSync(unixVenv)) return unixVenv;
    }
  }

  return 'python';
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
