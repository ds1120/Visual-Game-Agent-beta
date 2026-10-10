import subprocess
import unittest
from unittest.mock import Mock, patch

from app import launcher


class LauncherTests(unittest.TestCase):
    def test_normal_exit_stops_server_and_preserves_agent_exit_code(self):
        job = Mock()
        server = Mock(pid=10, _handle=100)
        agent = Mock(pid=11, _handle=101)
        server.poll.return_value = None
        agent.poll.return_value = 7
        agent.wait.return_value = 7
        with patch.object(launcher, 'create_process_job', return_value=job), \
             patch.object(launcher.subprocess, 'Popen', side_effect=[server, agent]), \
             patch.object(launcher.win32job, 'AssignProcessToJobObject') as assign:
            self.assertEqual(launcher.run_children(['server'], '.', ['agent'], Mock()), 7)
        self.assertEqual(assign.call_count, 2)
        job.Close.assert_called_once()
        server.kill.assert_called_once()
        server.wait.assert_called_once()

    def test_failed_agent_launch_stops_started_server(self):
        job = Mock()
        server = Mock(pid=10, _handle=100)
        server.poll.return_value = None
        with patch.object(launcher, 'create_process_job', return_value=job), \
             patch.object(launcher.subprocess, 'Popen', side_effect=[server, OSError('startup failed')]), \
             patch.object(launcher.win32job, 'AssignProcessToJobObject'):
            with self.assertRaises(OSError):
                launcher.run_children(['server'], '.', ['agent'], Mock())
        job.Close.assert_called_once()
        server.kill.assert_called_once()

    def test_ctrl_c_stops_both_processes_after_cleanup_timeout(self):
        job = Mock()
        server = Mock(pid=10, _handle=100)
        agent = Mock(pid=11, _handle=101)
        server.poll.return_value = agent.poll.return_value = None
        agent.wait.side_effect = [KeyboardInterrupt(), subprocess.TimeoutExpired('agent', 5), 0]
        with patch.object(launcher, 'create_process_job', return_value=job), \
             patch.object(launcher.subprocess, 'Popen', side_effect=[server, agent]), \
             patch.object(launcher.win32job, 'AssignProcessToJobObject'):
            self.assertEqual(launcher.run_children(['server'], '.', ['agent'], Mock()), 130)
        job.Close.assert_called_once()
        server.kill.assert_called_once()
        agent.kill.assert_called_once()

    def test_restart_cleanup_leaves_other_models_and_ports_running(self):
        def process(pid, model, port):
            value = Mock(pid=pid, info={'name': 'llama-server.exe'})
            value.exe.return_value = r'C:\server\llama-server.exe'
            value.cmdline.return_value = ['llama-server.exe', '-m', model, '--port', port]
            return value
        matching = process(1, 'qwen.gguf', '8082')
        other_model = process(2, 'other.gguf', '8082')
        other_port = process(3, 'qwen.gguf', '8083')
        with patch.object(launcher.psutil, 'process_iter', return_value=[matching, other_model, other_port]), \
             patch.object(launcher.psutil, 'wait_procs', return_value=([], [])):
            launcher.stop_previous_servers(r'C:\server\llama-server.exe', 'qwen.gguf')
        matching.terminate.assert_called_once()
        other_model.terminate.assert_not_called()
        other_port.terminate.assert_not_called()

    def test_job_is_configured_to_kill_children_when_handle_closes(self):
        job = Mock()
        limits = {'BasicLimitInformation': {'LimitFlags': 0}}
        with patch.object(launcher.win32job, 'CreateJobObject', return_value=job), \
             patch.object(launcher.win32job, 'QueryInformationJobObject', return_value=limits), \
             patch.object(launcher.win32job, 'SetInformationJobObject') as configure:
            self.assertIs(launcher.create_process_job(), job)
        self.assertTrue(limits['BasicLimitInformation']['LimitFlags'] & launcher.win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE)
        configure.assert_called_once()
