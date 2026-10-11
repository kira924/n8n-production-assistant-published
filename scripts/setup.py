"""Create a local, ignored configuration. Existing files are never overwritten."""
import os
import secrets
from pathlib import Path


def main():
    root = Path(__file__).resolve().parent.parent
    env_path = root / '.env'
    if env_path.exists():
        print('.env already exists; no values changed.')
        return
    text = '# Local secrets: do not commit or share this file.\n'
    text += 'PUBLIC_ORIGIN=http://localhost:8088\nPUBLIC_BIND_IP=127.0.0.1\nGROQ_API_KEY=\n'
    for name in ('WORKFLOW_SERVICE_TOKEN', 'REVIEWER_SECRET', 'SESSION_SECRET', 'N8N_ENCRYPTION_KEY'):
        text += name + '=' + secrets.token_urlsafe(48) + '\n'
    descriptor = os.open(env_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf-8', newline='') as stream:
        stream.write(text)
    print('Created .env with fresh server secrets. Add your Groq API key locally, then start Docker Compose.')


if __name__ == '__main__':
    main()
