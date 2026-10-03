"""Interactive token bootstrap: no password persistence, output, or duplicate token files."""
import argparse
import getpass
import os
from pathlib import Path
import sys
from garminconnect import Garmin
from garmin_mcp.token_utils import secure_token_dir

def main():
    from garmin_mcp.observability import configure_private_logging
    configure_private_logging()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--token-path', default=os.getenv('GARMINTOKENS','/data/garmin'))
    parser.add_argument('--verify', action='store_true')
    parser.add_argument('--force-reauth', action='store_true')
    args = parser.parse_args()
    os.umask(0o077)
    path = Path(args.token_path).expanduser()
    try:
        if args.verify:
            client = Garmin(retry_attempts=0)
            client.login(str(path))
            if not client.get_user_profile():
                raise RuntimeError('No profile')
            client.client.dump(str(path))
            secure_token_dir(str(path))
            print('Garmin authentication: PASS. Token permissions: owner only.')
            return
        if not sys.stdin.isatty():
            parser.error('Interactive terminal required; do not send credentials through chat or environment variables.')
        email = input('Garmin email: ').strip()
        password = getpass.getpass('Garmin password (hidden): ')
        client = Garmin(email=email, password=password, return_on_mfa=True, retry_attempts=0)
        state, context = client.login()
        if state == 'needs_mfa':
            client.resume_login(context, getpass.getpass('Garmin MFA (hidden): '))
        path.mkdir(parents=True,exist_ok=True,mode=0o700)
        client.client.dump(str(path))
        secure_token_dir(str(path))
        del password, email, client
        verifier = Garmin(retry_attempts=0)
        verifier.login(str(path))
        if not verifier.get_user_profile():
            raise RuntimeError('No profile')
        verifier.client.dump(str(path))
        secure_token_dir(str(path))
        print('Garmin authentication: PASS. Tokens saved securely; no password saved.')
    except Exception:
        print('Garmin authentication failed. Check credentials/MFA or wait after rate limiting. No secret details logged.',file=sys.stderr)
        raise SystemExit(1) from None

if __name__ == '__main__':
    main()
