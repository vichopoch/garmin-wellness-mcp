"""Prepare Railway root-owned volume, then permanently drop server privileges."""
import os
from pathlib import Path
import sys
os.umask(0o077)
path=Path(os.getenv('GARMINTOKENS','/data/garmin'))
if path.is_symlink():
    raise SystemExit('Token path cannot be a symlink')
path.mkdir(parents=True,exist_ok=True,mode=0o700)
if os.getuid() == 0:
    os.chown(path,10001,10001)
    os.chmod(path,0o700)
    for entry in path.iterdir():
        if entry.is_symlink(): raise SystemExit('Token directory contains unsafe symlink')
        if entry.is_file():
            os.chown(entry,10001,10001)
            os.chmod(entry,0o600)
    os.setgroups([])
    os.setgid(10001)
    os.setuid(10001)
os.execvp(sys.argv[1],sys.argv[1:])
