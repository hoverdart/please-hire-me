from pathlib import Path
import os
import signal
import subprocess
import sys
import time

from scripts.run_ci_tests import run_command

SCRIPT=Path(__file__).resolve().parents[1]/'scripts/run_ci_tests.py'


def test_ci_runner_preserves_success_and_output(capfd):
    assert run_command([sys.executable,'-c','print("synthetic CI child completed")'],5)==0
    assert 'synthetic CI child completed' in capfd.readouterr().out


def test_ci_runner_preserves_failure():
    assert run_command([sys.executable,'-c','raise SystemExit(7)'],5)==7


def test_ci_runner_bounds_captured_pytest_and_retains_its_stack(tmp_path):
    child=tmp_path/'test_hanging_child.py'
    child.write_text('import time\ndef test_hang():\n    time.sleep(60)\n')
    started=time.monotonic()
    result=subprocess.run([sys.executable,str(SCRIPT),'--timeout-seconds','5',str(child)],
                          cwd=tmp_path,capture_output=True,text=True,timeout=20)
    assert result.returncode==124
    assert time.monotonic()-started<15
    assert 'Test deadline exceeded' in result.stderr and 'test_hang' in result.stderr


def test_ci_runner_interrupt_stops_the_owned_pytest_child(tmp_path):
    ready=tmp_path/'ready';child=tmp_path/'test_interrupt.py'
    child.write_text('from pathlib import Path\nimport os,time\ndef test_wait():\n'
                     f'    Path({str(ready)!r}).write_text(str(os.getpid()))\n    time.sleep(60)\n')
    process=subprocess.Popen([sys.executable,str(SCRIPT),'--timeout-seconds','30',str(child)],
                             cwd=tmp_path,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    pid=None
    try:
        deadline=time.monotonic()+10
        while not ready.exists() and time.monotonic()<deadline:
            assert process.poll() is None
            time.sleep(.05)
        assert ready.exists(),'The synthetic pytest child did not start'
        pid=int(ready.read_text());process.send_signal(signal.SIGINT)
        _,error=process.communicate(timeout=10)
        assert process.returncode==130 and 'stopping its owned process group' in error
        try:os.kill(pid,0)
        except ProcessLookupError:pass
        else:raise AssertionError('The interrupted pytest child was left running')
    finally:
        if process.poll() is None:process.kill();process.wait(timeout=5)
        if pid:
            try:os.kill(pid,signal.SIGTERM)
            except ProcessLookupError:pass
