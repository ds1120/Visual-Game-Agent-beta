"""Own the Qwen server and GameBot in a kill-on-close Windows job."""
from pathlib import Path
import os
import subprocess
import sys

import psutil
import win32job


def option_value(arguments, names):
    for index, argument in enumerate(arguments):
        if argument in names and index + 1 < len(arguments):
            return arguments[index + 1]
        for name in names:
            if argument.startswith(name + '='):
                return argument.split('=', 1)[1]
    return None


def stop_previous_servers(executable, model, port=8082):
    """Clean only this executable/model/port, leaving other servers alone."""
    matching = []
    for process in psutil.process_iter(['name']):
        if (process.info['name'] or '').lower() != 'llama-server.exe':
            continue
        try:
            if os.path.normcase(process.exe()) != os.path.normcase(str(executable)):
                continue
            arguments = process.cmdline()
            if (option_value(arguments, ('--port',)) != str(port)
                    or os.path.normcase(option_value(arguments, ('-m', '--model')) or '')
                    != os.path.normcase(str(model))):
                continue
            print(f'[LAUNCHER] Stopping previous Qwen server PID={process.pid}', flush=True)
            process.terminate()
            matching.append(process)
        except psutil.NoSuchProcess:
            pass
    _, alive = psutil.wait_procs(matching, timeout=3)
    for process in alive:
        try:
            process.kill()
        except psutil.NoSuchProcess:
            pass
    _, alive = psutil.wait_procs(alive, timeout=3)
    if alive:
        raise RuntimeError('Previous Qwen server could not be stopped; refusing duplicate startup')


def create_process_job():
    job = win32job.CreateJobObject(None, '')
    try:
        limits = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
        limits['BasicLimitInformation']['LimitFlags'] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, limits)
    except BaseException:
        job.Close()
        raise
    return job


def run_children(server_command, server_directory, agent_command, log_file):
    job = create_process_job()
    children = []
    try:
        server = subprocess.Popen(server_command, cwd=server_directory,
                                  creationflags=subprocess.CREATE_NO_WINDOW,
                                  stdout=log_file, stderr=subprocess.STDOUT)
        children.append(server)
        win32job.AssignProcessToJobObject(job, int(server._handle))
        print(f'[LAUNCHER] Qwen server PID={server.pid}; asynchronous startup', flush=True)
        agent = subprocess.Popen(agent_command)
        children.append(agent)
        win32job.AssignProcessToJobObject(job, int(agent._handle))
        try:
            return agent.wait()
        except KeyboardInterrupt:
            # The console Ctrl+C event reaches the agent too; allow its cleanup.
            try:
                agent.wait(timeout=5)
            except (subprocess.TimeoutExpired, KeyboardInterrupt):
                pass
            return 130
    finally:
        # Windows also closes this handle if the launcher console is closed.
        job.Close()
        for process in children:
            if process.poll() is None:
                process.kill()  # Covers a child whose job assignment failed.
            process.wait()
        print('[LAUNCHER] GameBot and its Qwen server stopped', flush=True)


def main():
    directory = Path(os.environ['QWEN_SERVER_DIR'])
    executable = directory / 'llama-server.exe'
    model = Path(os.environ['QWEN_MODEL'])
    mmproj = Path(os.environ['QWEN_MMPROJ'])
    for path in (executable, model, mmproj):
        if not path.is_file():
            raise FileNotFoundError(path)
    stop_previous_servers(executable, model)
    server_command = [str(executable), '-m', str(model), '--mmproj', str(mmproj),
                      '--alias', os.environ['QWEN_MODEL_NAME'], '-ngl', '99',
                      '-c', os.environ['QWEN_CONTEXT_TOKENS'],
                      '-n', os.environ['QWEN_RESPONSE_TOKENS'], '--parallel', '1',
                      '-fa', 'on', '--host', '127.0.0.1', '--port', '8082']
    logs = Path(__file__).resolve().parent.parent / 'logs'
    logs.mkdir(exist_ok=True)
    with (logs / 'qwen-server.log').open('a', encoding='utf-8') as log_file:
        return run_children(server_command, str(directory),
                            [sys.executable, '-m', 'app.main', *sys.argv[1:]], log_file)


if __name__ == '__main__':
    raise SystemExit(main())
