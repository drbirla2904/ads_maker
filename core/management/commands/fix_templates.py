"""
Management command: fix_templates
Rescans ALL video templates in the database and updates their zone_data
with the correct union bbox and chroma detection.

Usage:
    python manage.py fix_templates
    python manage.py fix_templates --id 5       # fix specific template
    python manage.py fix_templates --dry-run    # preview without saving
"""
from django.core.management.base import BaseCommand
from templates_app.models import AdTemplate
from core.renderer import detect_chroma_color
import json


class Command(BaseCommand):
    help = 'Re-detect chroma zone for all video templates and update zone_data'

    def add_arguments(self, parser):
        parser.add_argument('--id', type=int, help='Fix only this template ID')
        parser.add_argument('--dry-run', action='store_true', help='Show what would change without saving')

    def handle(self, *args, **options):
        qs = AdTemplate.objects.filter(file_type='video')
        if options.get('id'):
            qs = qs.filter(pk=options['id'])

        total = qs.count()
        self.stdout.write(f"Found {total} video template(s) to process\n")

        for t in qs:
            self.stdout.write(f"\n[Template {t.pk}] '{t.title}'")
            self.stdout.write(f"  File: {t.file.name}")

            try:
                video_path = t.file.path
            except Exception as e:
                self.stdout.write(f"  ❌ Cannot access file: {e}")
                continue

            self.stdout.write(f"  Scanning all frames for chroma zone...")
            try:
                chroma = detect_chroma_color(video_path)
            except Exception as e:
                self.stdout.write(f"  ❌ Detection failed: {e}")
                continue

            if not chroma:
                self.stdout.write(f"  ⚠️  No chroma zone detected — skipping")
                continue

            # Build complete zone_data
            new_zone = {
                'chroma_hex':      chroma['hex'],
                'chroma_rgb':      list(chroma['rgb']),
                'chroma_sim':      chroma['similarity'],
                'chroma_blend':    chroma['blend'],
                'chroma_type':     chroma['type'],
                'chroma_coverage': chroma['coverage_pct'],
                'x':      chroma['bbox']['x'],
                'y':      chroma['bbox']['y'],
                'width':  chroma['bbox']['w'],
                'height': chroma['bbox']['h'],
                'bbox':   chroma['bbox'],
                'union_bbox': chroma.get('union_bbox', chroma['bbox']),
            }

            old_zone = t.get_zone()
            old_hex = old_zone.get('chroma_hex', 'NONE')
            old_bbox = old_zone.get('bbox', 'NONE')

            self.stdout.write(f"  Old: hex={old_hex} bbox={old_bbox}")
            self.stdout.write(f"  New: hex=#{chroma['hex'].upper()} bbox={chroma['bbox']}")

            if options.get('dry_run'):
                self.stdout.write(f"  [DRY RUN] Would update zone_data")
            else:
                t.zone_data = json.dumps(new_zone)
                t.save(update_fields=['zone_data'])
                self.stdout.write(f"  ✅ Updated zone_data")

        self.stdout.write(f"\n{'[DRY RUN] ' if options.get('dry_run') else ''}Done. {total} template(s) processed.")
