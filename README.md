# 🎨 One Click Designer — Ad Creation Platform

## Quick Start

```bash
# 1. Install dependencies
pip install django pillow ffmpeg-python numpy

# 2. Run migrations
python manage.py migrate

# 3. Create superuser (admin)
python manage.py createsuperuser

# 4. Start server
python manage.py runserver
```

## Access

| URL | Description |
|-----|-------------|
| http://localhost:8000/ | Homepage |
| http://localhost:8000/templates/ | Browse templates |
| http://localhost:8000/accounts/login/ | User login |
| http://localhost:8000/accounts/register/ | Sign up |
| http://localhost:8000/templates/admin/upload/ | **Admin: Upload template** |
| http://localhost:8000/templates/admin/list/ | **Admin: Manage templates** |
| http://localhost:8000/templates/admin/categories/ | **Admin: Categories** |

## Default Admin Login
- Username: `admin`
- Password: `admin123`

## How Admin Uploads a Template

1. Go to `/templates/admin/upload/`
2. Upload your image or video file
3. The file preview appears on canvas
4. **Draw the zone** — click and drag to mark the area users will replace
5. Adjust corners by dragging the green handles (for perspective)
6. Fill in title, tags, category
7. Upload!

## How Users Create Ads

1. Browse templates at `/templates/`
2. Click a template → "Use This Template"
3. Upload their image or video
4. System composites their content into the zone
5. Download the final HD ad

## Architecture

```
adplatform/
├── core/           — Home, dashboard, renderer engine
├── templates_app/  — Templates, ad creation, admin tools
├── accounts/       — Auth, user profiles
├── templates/      — All Django HTML templates (dark UI)
├── media/          — Uploaded files & rendered outputs
└── static/         — CSS/JS/images
```

## Zone Data Format

Zones are stored as JSON:
```json
{
  "points": [[x1,y1],[x2,y2],[x3,y3],[x4,y4]],
  "x": 100, "y": 200,
  "width": 400, "height": 300
}
```
The 4 points enable **perspective compositing** so user content
fits naturally into tilted/angled zones (like a billboard at an angle).

## Production Setup

1. Set `DEBUG = False` in settings.py
2. Set a real `SECRET_KEY`
3. Configure PostgreSQL database
4. Use Nginx + Gunicorn
5. Use Celery for background video processing
6. Use S3/Cloudflare R2 for file storage
