.PHONY: help sleep_5  start stop restart status  log log_watch log_clear clean  ipython  gitcreds

APP_COMMAND ?= python telegram_transmission_bot.py # The command to run in the background

# --- Variables ---
VENV_ACTIVATE_SCRIPT ?= .venv/bin/activate
ENVIRONMENT_FILE ?= .env

PID_FILE ?= /var/run/easysailbot.pid
APP_LOG_FILE ?= /var/log/easysailbot.log

GET_DESCENDANTS_SCRIPT ?= get_descendants.sh



# IMPORTANT: Ensure make uses a shell that understands 'source' as a built-in.
SHELL := /bin/bash

help:
	@echo "make start | stop | restart | status"
	@echo "   to control $(APP_COMMAND)"
	@echo ""
	@echo "make log | log_watch | log_clear"
	@echo "   to read $(APP_LOG_FILE)"
	@echo ""
	@echo 'eval "make gitcreds"'
	@echo "   to add git ssh key"
	@echo ""
	@echo "make ipython"
	@echo "   IPython in venv with environment variables"

requirements:
	apt install libglib2.0-dev libxml2-dev libxslt-dev
	python3 -m pip install requests cachetools transmissionrpc python-telegram-bot dlna-cast beautifulsoup4

sleep_5:
	@echo "Sleeping for 5 seconds"
	@sleep 5


log:
	@less "$(APP_LOG_FILE)"

log_watch:
	@tail -f "$(APP_LOG_FILE)"

log_clear:
	rm -f $(APP_LOG_FILE)

# --- Target to START the application in the background and its own process group ---
start:
	@echo "Starting application in the background..."

	@if [ -f "$(PID_FILE)" ]; then \
		echo "PID file '$(PID_FILE)' already exists. Application might already be running (PID: $$(cat $(PID_FILE)))."; \
		exit 1; \
	fi

	@source $(VENV_ACTIVATE_SCRIPT) && \
	source $(ENVIRONMENT_FILE) && \
	setsid nohup $(APP_COMMAND) > $(APP_LOG_FILE) 2>&1 & echo $$! > $(PID_FILE)

	@echo "Application '$(APP_COMMAND)' started. PID written to $(PID_FILE)"
	@echo "Check $(APP_LOG_FILE) for output."

# --- Target to STOP the application and all its children ---
stop:
	@echo "Stopping application and its child processes..."

	@if [ -f "$(PID_FILE)" ]; then \
		PID=$$(cat $(PID_FILE)); \
		if ps -p $$PID > /dev/null; then \
			DESCENDANTS=$$( $(SHELL) $(GET_DESCENDANTS_SCRIPT) $$PID ); \
			kill -- $$PID $$DESCENDANTS; \
			echo "Sent SIGTERM to $$PID $$DESCENDANTS."; \
			sleep 2; \
			if ! ps -p $$PID $$DESCENDANTS > /dev/null; then \
				echo "Successfully terminated $$PID $$DESCENDANTS"; \
				rm -f $(PID_FILE); \
			else \
				echo "Failed to terminate $$PID $$DESCENDANTS did not terminate. Trying kill -9 (SIGKILL)."; \
				kill -9 -- $$PID $$DESCENDANTS; \
				sleep 2; \
				if ! ps -p $$PID $$DESCENDANTS > /dev/null; then \
					echo "$$PID $$DESCENDANTS forcefully terminated."; \
					rm -f $(PID_FILE); \
				else \
					echo "Could not terminate $$PID $$DESCENDANTS. Manual intervention may be required."; \
					exit 1; \
				fi; \
			fi; \
		else \
			echo "PID file found ($(PID_FILE)), but process $$PID is NOT running. Removing stale PID file."; \
			rm -f $(PID_FILE); \
		fi; \
	else \
		echo "No PID file found. Application might not be running."; \
	fi

restart: stop start
	@echo "Restarted"

# --- Target to check status ---
status:
	@echo "Checking application status..."

	@if [ -f "$(PID_FILE)" ]; then \
		PID=$$(cat $(PID_FILE)); \
		if ps -p $$PID > /dev/null; then \
			echo "Application is RUNNING with PID (process group leader) $$PID."; \
			echo "Descendant processes (if any):"; \
			DESCENDANTS=$$( $(SHELL) $(GET_DESCENDANTS_SCRIPT) $$PID ); \
			if [ -n "$$DESCENDANTS" ]; then \
				ps -fp $$PID $$DESCENDANTS; \
			else \
				echo "  (No further descendants found)"; \
			fi; \
		else \
			echo "PID file found ($(PID_FILE)), but process $$PID (group leader) is NOT running. Removing stale PID file."; \
			rm -f $(PID_FILE); \
		fi; \
	else \
		echo "No PID file found. Application is NOT running."; \
	fi

clean: stop log_clear 

ipython:
	@source $(VENV_ACTIVATE_SCRIPT) && \
	source $(ENVIRONMENT_FILE) && \
	python -m IPython

gitcreds:
	@echo "echo 'SSH commands:'"
	@echo '    eval "$$(ssh-agent -s)"'
	@echo '    ssh-add ~/.ssh/id_ed25519_github'
