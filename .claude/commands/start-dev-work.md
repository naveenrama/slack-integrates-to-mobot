Start the development environment and app. Creates a feature branch if not already on one, sources the environment, and starts the app.

Steps:
1. If on `main` branch, create and switch to a new feature branch (ask for branch name)
2. Source the dev environment: `source mobot-in-slack-env/bin/activate`
3. Remove stale db: `rm -f mobot.db`
4. Start the app: `python app.py` (run in background)
5. Confirm it's running by checking for "Bolt app is running" in logs
