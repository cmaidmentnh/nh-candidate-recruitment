# NH Candidate Tracker - DigitalOcean Deployment

A Flask web application for tracking NH House candidates across election cycles.

## Features

- Dashboard showing 400 NH House seats across 203 districts
- Dual authentication (Candidates self-service vs Admin full access)
- Candidate management with status tracking (Confirmed, Potential, Considering, Declined, New Recruit)
- Comments and history audit trail
- Voter checklist matching
- CSV bulk import
- Registration tokens for controlled sign-ups
- Photo uploads to S3-compatible storage

## Deployment Options

### Option A: DigitalOcean App Platform (Easiest)

1. **Create a PostgreSQL Database**
   - Go to DigitalOcean → Databases → Create Database Cluster
   - Choose PostgreSQL 16
   - Select $15/mo plan (or cheaper dev database)
   - Note the connection string

2. **Create App Platform App**
   - Go to Apps → Create App
   - Connect your GitHub repo (or upload code)
   - Set environment variables (see `.env.example`)
   - Deploy

3. **Set Environment Variables in App Platform**
   ```
   DATABASE_URL=postgresql://...
   SECRET_KEY=your-secure-random-string
   ```

### Option B: Droplet + Docker (More Control)

1. **Create a Droplet**
   - Ubuntu 24.04, $6/mo (1GB RAM)
   - Enable backups (optional)

2. **SSH into droplet and install Docker**
   ```bash
   ssh root@your-droplet-ip
   apt update && apt upgrade -y
   apt install -y docker.io docker-compose
   systemctl enable docker
   ```

3. **Deploy the app**
   ```bash
   # Clone your repo or upload files
   cd /opt
   git clone your-repo candidate-tracker
   cd candidate-tracker
   
   # Create .env file
   cp .env.example .env
   nano .env  # Edit with your values
   
   # Build and run
   docker build -t candidate-tracker .
   docker run -d --name app -p 80:8080 --env-file .env candidate-tracker
   ```

4. **Set up SSL with Caddy (optional but recommended)**
   ```bash
   apt install -y caddy
   cat > /etc/caddy/Caddyfile << EOF
   yourdomain.com {
       reverse_proxy localhost:8080
   }
   EOF
   systemctl restart caddy
   ```

## Database Migration

### Export from Google Cloud

```bash
# On your local machine with cloud_sql_proxy running
pg_dump -h 127.0.0.1 -U postgres -d nh_candidates > backup.sql
```

### Import to DigitalOcean

```bash
# Get connection details from DigitalOcean database dashboard
psql "postgresql://user:password@host:25060/defaultdb?sslmode=require" < backup.sql
```

Or use the DigitalOcean console's "Import Database" feature.

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `DATABASE_URL` | Yes | PostgreSQL connection string |
| `SECRET_KEY` | Yes | Random string for session encryption |
| `PORT` | No | Default: 8080 |
| `DEBUG` | No | Set to "true" for development |
| `S3_ENDPOINT` | No* | DigitalOcean Spaces endpoint |
| `S3_BUCKET` | No* | Spaces bucket name |
| `S3_ACCESS_KEY` | No* | Spaces access key |
| `S3_SECRET_KEY` | No* | Spaces secret key |
| `S3_REGION` | No | Default: nyc3 |

*Required only if you want photo uploads

## Generate a Secret Key

```python
python3 -c "import secrets; print(secrets.token_hex(32))"
```

## First Admin User

After deployment, you'll need to create the first admin user directly in the database:

```sql
INSERT INTO users (username, email, password_hash, role, created_at)
VALUES (
  'admin',
  'admin@example.com',
  -- Generate this hash: python3 -c "from werkzeug.security import generate_password_hash; print(generate_password_hash('your-password'))"
  'scrypt:32768:8:1$...',
  'admin',
  NOW()
);
```

Then login at `/admin/login`

## Cost Estimate

| Service | Cost/Month |
|---------|------------|
| App Platform (Basic) | $5 |
| Managed PostgreSQL | $15 |
| Spaces (if needed) | $5 |
| **Total** | **$20-25/mo** |

Or with a Droplet:

| Service | Cost/Month |
|---------|------------|
| Droplet (1GB) | $6 |
| Managed PostgreSQL | $15 |
| Spaces (if needed) | $5 |
| **Total** | **$21-26/mo** |

This is significantly cheaper than Google Cloud App Engine + Cloud SQL.
