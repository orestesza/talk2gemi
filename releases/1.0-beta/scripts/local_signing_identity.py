#!/usr/bin/env python3
"""Reuse a per-Mac signing identity; private key stays non-exportable in login Keychain."""
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
LABEL='talk2gemi Local Code Signing'
def run(args):
    result=subprocess.run(args,capture_output=True,text=True)
    if result.returncode:
        raise RuntimeError(Path(args[0]).name+' failed: '+result.stderr.strip())
    return result.stdout

def main():
    existing=subprocess.run(['/usr/bin/security','find-certificate','-c',LABEL,'-Z'],capture_output=True,text=True)
    if existing.returncode==0:
        match=re.search(r'SHA-1 hash: ([0-9A-Fa-f]{40})',existing.stdout)
        if not match:raise RuntimeError('Cannot resolve existing local signing certificate')
        fingerprint=match.group(1)
        if fingerprint not in run(['/usr/bin/security','find-identity','-p','codesigning']):
            raise RuntimeError('Local signing certificate has no matching private key')
        print(fingerprint);return
    if existing.returncode!=44:raise RuntimeError('Cannot inspect local signing certificate')
    if '--create' not in sys.argv:raise RuntimeError('No local signing identity; run this script with --create once')
    openssl=shutil.which('openssl')
    if not openssl:raise RuntimeError('OpenSSL unavailable; cannot create signing identity')
    keychain=run(['/usr/bin/security','default-keychain','-d','user']).strip().strip('"')
    if not keychain:raise RuntimeError('No default user keychain')
    oldmask=os.umask(0o077)
    try:
        with tempfile.TemporaryDirectory(prefix='talk2gemi-signing-') as temp:
            tmp=Path(temp);key=tmp/'key.pem';cert=tmp/'cert.pem';bundle=tmp/'identity.p12';pw=tmp/'import.pass'
            password=secrets.token_urlsafe(40);pw.write_text(password)
            run([openssl,'req','-x509','-newkey','rsa:2048','-nodes','-keyout',str(key),'-out',str(cert),'-days','3650','-subj','/CN='+LABEL,
                '-addext','basicConstraints=critical,CA:FALSE','-addext','keyUsage=critical,digitalSignature','-addext','extendedKeyUsage=critical,codeSigning'])
            run([openssl,'pkcs12','-export','-inkey',str(key),'-in',str(cert),'-out',str(bundle),'-name',LABEL,'-passout','file:'+str(pw),'-keypbe','PBE-SHA1-3DES','-certpbe','PBE-SHA1-3DES','-macalg','sha1'])
            run(['/usr/bin/security','import',str(bundle),'-k',keychain,'-f','pkcs12','-P',password,'-x','-T','/usr/bin/codesign'])
            fingerprint=run([openssl,'x509','-in',str(cert),'-noout','-fingerprint','-sha1']).strip().split('=')[-1].replace(':','')
            print(fingerprint)
    finally:os.umask(oldmask)
if __name__=='__main__':
    try:main()
    except Exception as exc:
        print('Local code signing setup: '+str(exc),file=sys.stderr);sys.exit(1)
