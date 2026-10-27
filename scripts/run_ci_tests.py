"""Keep a complete test run inside the job budget and retain a stuck stack."""
import argparse
import os
import signal
import subprocess
import sys
import time


def _stop(process,grace_seconds):
    if process.poll() is None:
        try:os.killpg(process.pid,signal.SIGTERM)
        except ProcessLookupError:pass
    try:process.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        try:os.killpg(process.pid,signal.SIGKILL)
        except ProcessLookupError:pass
        try:process.wait(timeout=grace_seconds)
        except subprocess.TimeoutExpired:
            print('The owned test process did not exit after SIGKILL.',file=sys.stderr,flush=True)


def run_command(command,timeout_seconds,grace_seconds=5):
    # An owned process group keeps timeout cleanup away from the runner itself.
    process=subprocess.Popen(command,start_new_session=True)
    try:
        return process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        print(f'Test deadline exceeded after {timeout_seconds:g}s; requesting thread stacks.',file=sys.stderr,flush=True)
        if process.poll() is None:
            try:os.kill(process.pid,signal.SIGUSR2)
            except ProcessLookupError:pass
            time.sleep(.5)
        _stop(process,grace_seconds)
        return 124
    except KeyboardInterrupt:
        print('Test command interrupted; stopping its owned process group.',file=sys.stderr,flush=True)
        _stop(process,grace_seconds)
        return 130


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--timeout-seconds',type=float,default=1800)
    parser.add_argument('pytest_args',nargs='*')
    args=parser.parse_args()
    if not 0<args.timeout_seconds<=1800:parser.error('Use a test deadline between 0 and 1800 seconds.')
    if os.name!='posix':parser.error('This CI runner supports macOS and Linux.')
    # A signal handler avoids a watchdog thread for every individual test.
    bootstrap=('import faulthandler,os,signal,sys,pytest; '
               'faulthandler.register(signal.SIGUSR2,file=os.dup(2),all_threads=True); '
               'raise SystemExit(pytest.main(sys.argv[1:]))')
    command=[sys.executable,'-c',bootstrap,'-x','-q','--tb=short',*args.pytest_args]
    print(f'Running the complete requested pytest selection with a {args.timeout_seconds:g}s deadline.',flush=True)
    result=run_command(command,args.timeout_seconds)
    return result if result>=0 else 1


if __name__=='__main__':raise SystemExit(main())
