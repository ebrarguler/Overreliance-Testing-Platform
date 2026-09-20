# Setup — macOS

## Prerequisites

- **Python 3.10+** — `python3 --version`
- **Docker Desktop** — needed to run MongoDB locally; download from <https://www.docker.com/products/docker-desktop>
- An **OpenAI API key** — <https://platform.openai.com/api-keys>

---

## 1. Clone the repo and create a virtual environment

```bash
git clone https://github.com/ebrarguler/Overreliance-Testing-Platform.git
cd Overreliance-Testing-Platform

python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
```

---

## 2. Start MongoDB with Docker

```bash
docker run --name overreliance-mongo \
  -d \
  -p 27017:27017 \
  -e MONGO_INITDB_ROOT_USERNAME=admin \
  -e MONGO_INITDB_ROOT_PASSWORD=password \
  mongo:7
```

The container is named `overreliance-mongo` and maps the default MongoDB port to `localhost:27017`.

To stop and restart it later:

```bash
docker stop overreliance-mongo
docker start overreliance-mongo
```

To wipe all data and start fresh:

```bash
docker rm -f overreliance-mongo
# then re-run the docker run command above
```

---

## 3. Create the .env file

Copy the example file and fill in your values:

```bash
cp .env.example .env
```

Then edit `.env`:

```
APP_SECRET_KEY=<any long random string>
API_KEY=sk-<your OpenAI key>

MONGODB_UID=mongodb://admin
MONGODB_PWD=password
MONGODB_CLUSTER_NAME=localhost:27017
MONGODB_AUTH=/?authSource=admin
```

The four `MONGODB_*` values above match the Docker container started in step 2. Change `MONGODB_PWD` if you used a different password in the `docker run` command.

> **Note:** `db.py` assembles the MongoDB URI by concatenating these variables as
> `{MONGODB_UID}:{MONGODB_PWD}@{MONGODB_CLUSTER_NAME}{MONGODB_AUTH}`.
> `MONGODB_UID` carries the `mongodb://` scheme prefix **and** the username.
> Do not add a leading `@` to `MONGODB_CLUSTER_NAME` — the code inserts the
> separator itself.

---

## 4. Run the app

```bash
source .venv/bin/activate   # if not already active
flask --app app run --debug
```

The app is available at <http://127.0.0.1:5001>.

---

## Project structure

```
Overreliance-Testing-Platform/
├── app/
│   ├── __init__.py          # App factory
│   ├── routes.py            # All routes
│   ├── templates/           # Jinja2 templates
│   ├── static/css/
│   └── utils/
│       ├── db.py            # MongoDB helpers
│       ├── questions.py     # Question bank and AI prompts
│       ├── demographics.py
│       ├── consents.py
│       └── encryption_utils.py
├── .env                     # Local secrets — gitignored, never commit
├── .env.example             # Template for .env
├── app.py                   # Production entry point (gunicorn)
├── run.py                   # Development entry point
├── Procfile                 # gunicorn command for Heroku/Render
└── requirements.txt
```
