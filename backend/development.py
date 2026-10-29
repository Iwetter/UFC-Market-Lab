"""A small persistent lease for cooperating development runs, not market ticks."""
import json
import secrets
import time
from contextlib import contextmanager
from pathlib import Path

from .operations import writer_lease


class DevelopmentLease:
    def __init__(self,directory):
        self.directory=Path(directory)
        self.path=self.directory/'development-lease.json'

    def _read(self):
        return json.loads(self.path.read_text()) if self.path.exists() else None

    def acquire(self,owner,*,ttl_seconds=7200,now=None):
        if not owner or not 60<=ttl_seconds<=14400:raise ValueError('owner and a 1-minute to 4-hour lease required')
        stamp=time.time() if now is None else now
        self.directory.mkdir(parents=True,exist_ok=True)
        with writer_lease(self.directory/'development-coordination'):
            existing=self._read()
            if existing and existing['expires_at']>stamp:return {'acquired':False,'owner':existing['owner'],'expires_at':existing['expires_at']}
            body={'owner':owner,'token':secrets.token_hex(16),'created_at':stamp,'expires_at':stamp+ttl_seconds}
            temp=self.path.with_suffix('.tmp');temp.write_text(json.dumps(body));temp.replace(self.path)
            return {'acquired':True,**body}

    def release(self,token):
        with writer_lease(self.directory/'development-coordination'):
            body=self._read()
            if not body:return False
            if not secrets.compare_digest(body['token'],token):raise ValueError('development lease belongs to another run')
            self.path.unlink()
            return True

    def status(self,now=None):
        body=self._read()
        if not body:return {'active':False}
        stamp=time.time() if now is None else now
        return {'active':body['expires_at']>stamp,'owner':body['owner'],'expires_at':body['expires_at']}
