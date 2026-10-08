"""Valeurs par défaut sûres de core/settings.py (chargées dans un processus séparé)."""

import json
import os
import subprocess
import sys
from pathlib import Path

from django.test import SimpleTestCase

RACINE = Path(__file__).resolve().parents[2]

AFFICHER = (
    "import json, core.settings as s; print(json.dumps({"
    "'DEBUG': s.DEBUG, 'ALLOWED_HOSTS': s.ALLOWED_HOSTS,"
    "'CORS_ALL': getattr(s, 'CORS_ALLOW_ALL_ORIGINS', False),"
    "'SSL': getattr(s, 'SECURE_SSL_REDIRECT', False), 'HSTS': getattr(s, 'SECURE_HSTS_SECONDS', 0),"
    "'COOKIE': getattr(s, 'SESSION_COOKIE_SECURE', False)}))"
)


def charger_reglages(**env):
    # Environnement minimal : rien hérité du .env ni du processus de test.
    environnement = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(RACINE), **env}
    return subprocess.run(
        [sys.executable, "-c", AFFICHER], cwd="/", env=environnement, capture_output=True, text=True,
    )


class ReglagesTests(SimpleTestCase):

    def test_production_par_defaut_et_secrets_obligatoires(self):
        resultat = charger_reglages()
        self.assertNotEqual(resultat.returncode, 0)
        self.assertIn("DJANGO_SECRET_KEY", resultat.stderr)

        resultat = charger_reglages(DJANGO_SECRET_KEY="s" * 50)
        self.assertNotEqual(resultat.returncode, 0)
        self.assertIn("HMAC_CLE_V1", resultat.stderr)

    def test_production_durcie(self):
        resultat = charger_reglages(DJANGO_SECRET_KEY="s" * 50, HMAC_CLE_V1="k" * 64)
        self.assertEqual(resultat.returncode, 0, resultat.stderr)
        reglages = json.loads(resultat.stdout)
        self.assertFalse(reglages["DEBUG"])
        self.assertEqual(reglages["ALLOWED_HOSTS"], [])
        self.assertFalse(reglages["CORS_ALL"])
        self.assertTrue(reglages["SSL"])
        self.assertGreaterEqual(reglages["HSTS"], 31536000)
        self.assertTrue(reglages["COOKIE"])

    def test_developpement_explicite(self):
        resultat = charger_reglages(DJANGO_DEBUG="True")
        self.assertEqual(resultat.returncode, 0, resultat.stderr)
        reglages = json.loads(resultat.stdout)
        self.assertTrue(reglages["DEBUG"])
        self.assertTrue(reglages["CORS_ALL"])
