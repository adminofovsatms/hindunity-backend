## Hindunity Backend (Flask)

This is a small Flask-based backend that exposes endpoints for:

- **Health check**: `/health`
- **S3 upload URL generation**: `/api/get-upload-url`, `/api/get-avatar-upload-url`
- **Bot post creation**: `/botposts`, `/pendingbotposts`
- **Media deletion**: `/delete-media`

### Setup

1. **Create and activate a virtual environment (optional but recommended)**:

```bash
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
```

2. **Install dependencies**:

```bash
pip install -r requirements.txt
```

3. **Configure environment variables** (e.g. in a `.env` file in the project root):

- **Supabase**
  - `SUPABASE_URL`
  - `SUPABASE_KEY`
  - `BOT_EMAIL`
  - `BOT_PASSWORD`
- **AWS**
  - `AWS_ACCESS_KEY_ID`
  - `AWS_SECRET_ACCESS_KEY`
  - `AWS_REGION` (optional, defaults to `us-east-1`)
  - `AWS_S3_BUCKET`
- **Storage**
  - `SUPABASE_STORAGE_BUCKET` (optional, defaults to `tweets-media`)

### Running the app

Run the Flask application with:

```bash
python app.py
```

By default it will listen on `http://0.0.0.0:5001`.


