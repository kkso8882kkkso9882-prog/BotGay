MoonVeil Discord Bot — All-in-One package

Files:
- bot.py: one Python file containing the deobfuscator and Discord bot
- requirements.txt: Python dependencies
- Dockerfile: optional runtime with Lua 5.1 installed
- .env.example: shows the required environment variable name

Bot-Hosting.net setup:
1. Upload/extract this ZIP into the server files.
2. Install dependencies from requirements.txt (startup/install command: pip install -r requirements.txt).
3. Set DISCORD_TOKEN in the host's environment variables; never publish the token.
4. Set the entry/start command to: python bot.py --bot
5. Lua 5.1 must be installed and available as lua5.1/lua51/lua. If the host does not provide Lua 5.1 and does not allow installing system packages, this bot will not start; use a host/container that supports Lua 5.1.

Discord:
- Create a bot in Discord Developer Portal.
- Invite it with scopes bot and applications.commands.
- Use /moonveil with a direct public HTTP(S) URL to a Lua file.

Runtime requirements:
- Python 3.10+
- Lua 5.1
- Outbound HTTPS access to Discord and the file host

Security/limits:
- The bot rejects redirects and non-public IP destinations to reduce SSRF risk.
- Source and output sizes, download time, and concurrent jobs are limited by the Python script.
- Only analyze code you have permission to inspect.
- This is syntax-checked but has not been live-tested with a real Discord token or on Bot-Hosting.net. Verify Lua 5.1 availability on the assigned host.
