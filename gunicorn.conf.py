"""
Configuration gunicorn, chargée automatiquement (fichier gunicorn.conf.py
dans le dossier de lancement), quelle que soit la commande de démarrage
configurée sur l'hébergeur.

Par défaut gunicorn lance UN processus qui traite UNE requête à la fois :
le portail, qui envoie plusieurs requêtes en parallèle (tableau de bord,
notifications, configuration...), les voyait passer en file d'attente.
Ici : 2 processus x 4 fils = 8 requêtes simultanées, ce qui tient dans les
512 Mo de l'offre gratuite de Render.
"""

import os

workers = int(os.getenv("WEB_CONCURRENCY", 2))
threads = int(os.getenv("GUNICORN_THREADS", 4))
worker_class = "gthread"
# Laisse le temps aux générations de PDF / exports, sans bloquer indéfiniment.
timeout = int(os.getenv("GUNICORN_TIMEOUT", 60))
# Le port est fourni par Render dans $PORT.
bind = f"0.0.0.0:{os.getenv('PORT', '8000')}"
accesslog = "-"
