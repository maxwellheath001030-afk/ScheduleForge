# ScheduleForge v1.5 — Deployable Live UVU Build

This version is packaged as a real web service rather than a double-click HTML file.

## What it contains
- `server.py` — Flask backend and UVU public Banner adapter
- `ScheduleForge.html` — main interface
- `live-test.html` — live UVU diagnostic interface
- `requirements.txt` — Python dependencies
- `render.yaml` — deployment configuration
- `Procfile` — production start command

## Deploy
1. Put these files in a GitHub repository.
2. In Render, create a Blueprint/Web Service from that repository.
3. Render can use `render.yaml`; otherwise use:
   - Build: `pip install -r requirements.txt`
   - Start: `gunicorn server:app`
4. Open the generated HTTPS URL.
5. Add `/live-test.html` to the URL for the UVU live-data tester.

## Safety/validation behavior
The app does not guess a Banner term code. It only enables a Spring 2027 live lookup
after the UVU term resolver returns one matching Spring 2027 term. If UVU changes its
public Banner endpoints, the diagnostic response reports failure instead of silently
showing another semester or demo data.

## Current development status
Deployment-ready does NOT mean Spring 2027 live retrieval has already been proven.
The deployed server is intended to perform that real-world verification from the host.
