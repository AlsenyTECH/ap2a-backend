"""
adhesion/certificats.py

Génération de certificats de formation en PDF avec QR code de
vérification. Utilise ReportLab pour le PDF et qrcode pour le QR.

Le certificat contient :
- Logo et nom de l'association en en-tête
- Sceau doré décoratif et bandeau diagonal aux couleurs de l'AP2A
- Identité du participant (nom, prénom) en évidence
- Titre de la formation, dates, lieu et code de session
- Taux de présence et mention
- QR code de vérification (pointe vers une URL unique)
- Numéro de certificat unique, date d'émission et signatures
- Cadre décoratif pour un rendu professionnel

Usage depuis une vue :
    from adhesion.certificats import generer_certificat_pdf
    pdf_bytes = generer_certificat_pdf(participant, cohorte, taux_presence)
"""

import io
import qrcode
from datetime import date

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import cm
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader

from .models import ConfigAssociation

# Palette officielle du certificat AP2A (reprise à l'identique sur les
# badges pour que les deux documents se reconnaissent comme venant du
# même émetteur).
COULEUR_NAVY = colors.HexColor("#1a3a5c")
COULEUR_NAVY_CLAIR = colors.HexColor("#2f5680")
COULEUR_OR = colors.HexColor("#c9a84c")
COULEUR_OR_CLAIR = colors.HexColor("#e4cc85")
COULEUR_FOND = colors.HexColor("#fdfcf9")
COULEUR_TEXTE = colors.HexColor("#333333")
COULEUR_TEXTE_DOUX = colors.HexColor("#666666")


def _generer_qr_code(donnees: str, taille: int = 200) -> ImageReader:
    """
    Génère un QR code en mémoire et le retourne comme ImageReader
    pour insertion dans le PDF ReportLab.
    """
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8,
        border=2,
    )
    qr.add_data(donnees)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")

    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)
    return ImageReader(buffer)


def _charger_logo_reader(config) -> ImageReader | None:
    """
    Charge le logo de l'association configuré dans ConfigAssociation,
    s'il existe. Retourne None plutôt que de planter si aucun logo
    n'est encore renseigné (Configuration Asso).
    """
    if not config.logo:
        return None
    try:
        return ImageReader(config.logo.path)
    except Exception:
        return None


def _dessiner_ruban_coin(c, coin, taille, epaisseur_or=0.45 * cm):
    """
    Dessine un triangle de coin façon "ruban de marque" - navy avec un
    fin liseré or le long de son bord diagonal - strictement confiné
    à un carré `taille` x `taille` ancré au coin choisi ('haut-gauche'
    ou 'bas-droite'), pour ne jamais empiéter sur le contenu du
    certificat quel que soit l'agencement du texte central.
    """
    c.saveState()

    if coin == "haut-gauche":
        ax, ay = 0, landscape(A4)[1]
        p_or = c.beginPath()
        p_or.moveTo(ax, ay)
        p_or.lineTo(ax + taille, ay)
        p_or.lineTo(ax, ay - taille)
        p_or.close()
        c.setFillColor(COULEUR_OR)
        c.drawPath(p_or, fill=1, stroke=0)

        t2 = taille - epaisseur_or
        p_navy = c.beginPath()
        p_navy.moveTo(ax, ay)
        p_navy.lineTo(ax + t2, ay)
        p_navy.lineTo(ax, ay - t2)
        p_navy.close()
        c.setFillColor(COULEUR_NAVY)
        c.drawPath(p_navy, fill=1, stroke=0)
    else:  # bas-droite
        ax, ay = landscape(A4)[0], 0
        p_or = c.beginPath()
        p_or.moveTo(ax, ay)
        p_or.lineTo(ax - taille, ay)
        p_or.lineTo(ax, ay + taille)
        p_or.close()
        c.setFillColor(COULEUR_OR)
        c.drawPath(p_or, fill=1, stroke=0)

        t2 = taille - epaisseur_or
        p_navy = c.beginPath()
        p_navy.moveTo(ax, ay)
        p_navy.lineTo(ax - t2, ay)
        p_navy.lineTo(ax, ay + t2)
        p_navy.close()
        c.setFillColor(COULEUR_NAVY)
        c.drawPath(p_navy, fill=1, stroke=0)

    c.restoreState()


def _dessiner_sceau(c, cx, cy, rayon=1.55 * cm):
    """Sceau doré décoratif (médaille d'excellence) avec rubans, coin supérieur droit."""
    c.saveState()

    # Rubans du sceau (deux pennons sous le médaillon)
    c.setFillColor(COULEUR_NAVY)
    c.setLineJoin(0)
    for decalage in (-0.55 * cm, 0.55 * cm):
        p = c.beginPath()
        p.moveTo(cx + decalage - 0.42 * cm, cy - rayon * 0.35)
        p.lineTo(cx + decalage + 0.42 * cm, cy - rayon * 0.35)
        p.lineTo(cx + decalage + 0.42 * cm, cy - rayon * 1.9)
        p.lineTo(cx + decalage, cy - rayon * 1.55)
        p.lineTo(cx + decalage - 0.42 * cm, cy - rayon * 1.9)
        p.close()
        c.drawPath(p, fill=1, stroke=0)

    # Médaillon : anneaux concentriques or / navy / or
    c.setFillColor(COULEUR_OR)
    c.circle(cx, cy, rayon, fill=1, stroke=0)
    c.setFillColor(COULEUR_OR_CLAIR)
    c.circle(cx, cy, rayon * 0.82, fill=1, stroke=0)
    c.setFillColor(COULEUR_NAVY)
    c.circle(cx, cy, rayon * 0.64, fill=1, stroke=0)

    # Étoile à 5 branches au centre, symbole d'excellence
    import math
    c.setFillColor(COULEUR_OR)
    points = []
    for i in range(10):
        r = rayon * 0.42 if i % 2 == 0 else rayon * 0.18
        theta = math.pi / 2 + i * math.pi / 5
        points.append((cx + r * math.cos(theta), cy + r * math.sin(theta)))
    p = c.beginPath()
    p.moveTo(*points[0])
    for pt in points[1:]:
        p.lineTo(*pt)
    p.close()
    c.drawPath(p, fill=1, stroke=0)

    c.restoreState()


def _dessiner_cadre_decoratif(c, largeur, hauteur):
    """Dessine un double cadre décoratif autour du certificat."""
    c.setStrokeColor(COULEUR_NAVY)
    c.setLineWidth(2.5)
    c.rect(1.3 * cm, 1.3 * cm, largeur - 2.6 * cm, hauteur - 2.6 * cm)

    c.setStrokeColor(COULEUR_OR)
    c.setLineWidth(1)
    c.rect(1.55 * cm, 1.55 * cm, largeur - 3.1 * cm, hauteur - 3.1 * cm)


def _dessiner_certificat(c, participant, cohorte, taux_presence, config, logo_reader, numero_certificat, url_verification):
    """
    Dessine un certificat complet sur le canevas `c`, à l'emplacement
    courant (une page pleine, paysage A4). Factorisé pour être appelé
    identiquement par la génération unitaire et la génération en lot -
    les deux doivent produire des documents visuellement identiques.
    """
    largeur, hauteur = landscape(A4)

    nom_asso = (config.nom_association or "AP2A").upper()
    slogan = config.slogan or "Association Parcelles Assainies en Action"

    # ====== ARRIÈRE-PLAN ======
    c.setFillColor(COULEUR_FOND)
    c.rect(0, 0, largeur, hauteur, fill=True, stroke=False)

    # Bandeau de marque : ruban navy/or dans le coin haut-gauche, écho
    # plus discret dans le coin bas-droit (même geste graphique que les
    # badges, pour que les deux documents se reconnaissent) - strictement
    # confiné à leur coin, jamais au-dessus du texte central.
    _dessiner_ruban_coin(c, "haut-gauche", 6.5 * cm)
    _dessiner_ruban_coin(c, "bas-droite", 3.2 * cm, epaisseur_or=0.3 * cm)

    _dessiner_cadre_decoratif(c, largeur, hauteur)
    _dessiner_sceau(c, largeur - 3.6 * cm, hauteur - 3.4 * cm)

    # ====== EN-TÊTE ======
    y = hauteur - 2.7 * cm

    if logo_reader is not None:
        logo_h = 1.9 * cm
        logo_w = logo_h * 1.35
        c.drawImage(
            logo_reader, largeur / 2 - logo_w / 2, y - logo_h,
            width=logo_w, height=logo_h, mask="auto", preserveAspectRatio=True,
        )
        y -= logo_h + 0.35 * cm
    else:
        c.setFillColor(COULEUR_NAVY)
        c.setFont("Helvetica-Bold", 22)
        c.drawCentredString(largeur / 2, y, nom_asso)
        y -= 0.9 * cm

    c.setFont("Helvetica-Oblique", 9.5)
    c.setFillColor(COULEUR_TEXTE_DOUX)
    c.drawCentredString(largeur / 2, y, slogan)

    y -= 0.75 * cm
    c.setStrokeColor(COULEUR_OR)
    c.setLineWidth(1.5)
    c.line(6 * cm, y, largeur - 6 * cm, y)

    # ====== TITRE ======
    y -= 1.35 * cm
    c.setFillColor(COULEUR_NAVY)
    c.setFont("Helvetica-Bold", 26)
    c.drawCentredString(largeur / 2, y, "CERTIFICAT DE FORMATION")

    # ====== CORPS ======
    y -= 1.15 * cm
    c.setFillColor(COULEUR_TEXTE)
    c.setFont("Helvetica-Oblique", 12)
    c.drawCentredString(largeur / 2, y, "Décerné à")

    # Nom du participant
    y -= 1.25 * cm
    c.setFillColor(COULEUR_NAVY)
    c.setFont("Times-BoldItalic", 30)
    nom_complet = f"{participant.prenom} {participant.nom}"
    c.drawCentredString(largeur / 2, y, nom_complet)

    y -= 0.35 * cm
    c.setStrokeColor(COULEUR_OR)
    c.setLineWidth(1.2)
    text_width = c.stringWidth(nom_complet, "Times-BoldItalic", 30)
    c.line(largeur / 2 - text_width / 2 - 1 * cm, y, largeur / 2 + text_width / 2 + 1 * cm, y)

    # Formation
    y -= 1 * cm
    c.setFillColor(COULEUR_TEXTE)
    c.setFont("Helvetica", 12)
    c.drawCentredString(largeur / 2, y, "pour avoir suivi avec assiduité et succès la formation")

    y -= 0.9 * cm
    c.setFillColor(COULEUR_NAVY)
    c.setFont("Helvetica-Bold", 16)
    c.drawCentredString(largeur / 2, y, f"« {cohorte.formation.titre} »")

    # Cohorte, dates et lieu
    y -= 0.8 * cm
    c.setFillColor(COULEUR_TEXTE_DOUX)
    c.setFont("Helvetica", 10.5)
    date_debut = cohorte.date_debut.strftime("%d/%m/%Y") if cohorte.date_debut else "N/A"
    date_fin = cohorte.date_fin.strftime("%d/%m/%Y") if cohorte.date_fin else "N/A"
    lieu_str = f"  •  {cohorte.lieu}" if cohorte.lieu else ""
    c.drawCentredString(
        largeur / 2, y,
        f"Session {cohorte.code_cohorte}  •  Du {date_debut} au {date_fin}{lieu_str}",
    )

    # Taux de présence et mention
    y -= 0.7 * cm
    if taux_presence >= 90:
        mention = "avec la mention Excellent"
    elif taux_presence >= 75:
        mention = "avec la mention Bien"
    else:
        mention = ""

    c.setFont("Helvetica", 10.5)
    c.setFillColor(COULEUR_TEXTE_DOUX)
    texte_presence = f"Taux de présence : {taux_presence:.0f}%"
    if mention:
        texte_presence += f"  —  {mention}"
    c.drawCentredString(largeur / 2, y, texte_presence)

    # ====== BAS DU CERTIFICAT ======
    y_bas = 3.9 * cm

    # QR Code à gauche
    url_qr = url_verification or f"https://ap2a.org/verifier-certificat/{numero_certificat}"
    qr_image = _generer_qr_code(url_qr)
    c.drawImage(qr_image, 3.2 * cm, y_bas - 0.4 * cm, 2.8 * cm, 2.8 * cm)
    c.setFont("Helvetica", 6.5)
    c.setFillColor(COULEUR_TEXTE_DOUX)
    c.drawCentredString(4.6 * cm, y_bas - 0.75 * cm, "Scanner pour vérifier")

    # Date d'émission et numéro, au centre
    c.setFont("Helvetica", 9.5)
    c.setFillColor(COULEUR_TEXTE_DOUX)
    date_emission = date.today().strftime("%d/%m/%Y")
    c.drawCentredString(largeur / 2, y_bas + 1.7 * cm, f"Délivré le {date_emission}")
    c.setFont("Helvetica", 7.5)
    c.setFillColor(colors.HexColor("#999999"))
    c.drawCentredString(largeur / 2, y_bas + 0.95 * cm, f"N° {numero_certificat}")

    # Signatures à droite : formateur + association
    formateur = cohorte.formateur or "Le Formateur"
    c.setStrokeColor(colors.HexColor("#999999"))
    c.setLineWidth(0.6)

    x_sig1 = largeur - 12.5 * cm
    c.line(x_sig1, y_bas + 1.3 * cm, x_sig1 + 4.3 * cm, y_bas + 1.3 * cm)
    c.setFont("Helvetica-Bold", 9)
    c.setFillColor(COULEUR_TEXTE)
    c.drawCentredString(x_sig1 + 2.15 * cm, y_bas + 0.75 * cm, formateur)
    c.setFont("Helvetica-Oblique", 7.5)
    c.setFillColor(COULEUR_TEXTE_DOUX)
    c.drawCentredString(x_sig1 + 2.15 * cm, y_bas + 0.3 * cm, "Formateur")

    x_sig2 = largeur - 7.3 * cm
    c.line(x_sig2, y_bas + 1.3 * cm, x_sig2 + 4.3 * cm, y_bas + 1.3 * cm)
    c.setFont("Helvetica-Bold", 9)
    c.setFillColor(COULEUR_TEXTE)
    c.drawCentredString(x_sig2 + 2.15 * cm, y_bas + 0.75 * cm, f"Pour l'{nom_asso}")
    c.setFont("Helvetica-Oblique", 7.5)
    c.setFillColor(COULEUR_TEXTE_DOUX)
    c.drawCentredString(x_sig2 + 2.15 * cm, y_bas + 0.3 * cm, "La Direction")


def generer_certificat_pdf(
    participant,
    cohorte,
    taux_presence: float,
    url_verification: str = None,
) -> bytes:
    """
    Génère un certificat de formation au format PDF (paysage A4).

    Args:
        participant: Instance du modèle Participant
        cohorte: Instance du modèle Cohorte (avec formation liée)
        taux_presence: float entre 0 et 100
        url_verification: URL de base pour le QR (optionnelle)

    Returns:
        bytes: Contenu du fichier PDF
    """
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=landscape(A4))

    config = ConfigAssociation.charger()
    logo_reader = _charger_logo_reader(config)
    numero_certificat = f"CERT-{cohorte.code_cohorte}-{participant.id_participant:04d}"

    _dessiner_certificat(c, participant, cohorte, taux_presence, config, logo_reader, numero_certificat, url_verification)

    c.showPage()
    c.save()
    buffer.seek(0)
    return buffer.getvalue()


def generer_certificats_cohorte(cohorte) -> list:
    """
    Génère les certificats pour tous les participants éligibles d'une cohorte.

    Éligibilité : taux de présence >= seuil de certification de la cohorte.

    Returns:
        list de tuples (participant, pdf_bytes, taux_presence, eligible)
    """
    from .models import InscriptionCohorte, SeanceCohorte, PresenceCohorte

    seances = SeanceCohorte.objects.filter(cohorte=cohorte)
    nb_seances = seances.count()
    seuil = cohorte.seuil_certification or 75

    if nb_seances == 0:
        return []

    inscriptions = InscriptionCohorte.objects.filter(
        cohorte=cohorte, statut="INSCRIT"
    ).select_related("participant")

    resultats = []

    for inscription in inscriptions:
        participant = inscription.participant
        nb_presences = PresenceCohorte.objects.filter(
            participant=participant,
            seance_cohorte__cohorte=cohorte,
        ).count()

        taux = (nb_presences / nb_seances) * 100
        eligible = taux >= seuil

        pdf_bytes = None
        if eligible:
            pdf_bytes = generer_certificat_pdf(participant, cohorte, taux)

        resultats.append((participant, pdf_bytes, taux, eligible))

    return resultats


def generer_certificats_lot_pdf(inscriptions_cohorte) -> bytes:
    """
    Génère un unique fichier PDF multi-pages (format A4 Paysage)
    contenant les certificats pour tous les participants fournis.
    """
    from .models import SeanceCohorte, PresenceCohorte

    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=landscape(A4))

    config = ConfigAssociation.charger()
    logo_reader = _charger_logo_reader(config)

    count = 0
    for insc in inscriptions_cohorte:
        participant = insc.participant
        cohorte = insc.cohorte

        seances = SeanceCohorte.objects.filter(cohorte=cohorte)
        nb_seances = seances.count()
        nb_presences = PresenceCohorte.objects.filter(
            participant=participant,
            seance_cohorte__cohorte=cohorte,
        ).count()
        taux_presence = (nb_presences / nb_seances * 100) if nb_seances > 0 else 100

        numero_certificat = f"CERT-{cohorte.code_cohorte}-{participant.id_participant:04d}"

        _dessiner_certificat(c, participant, cohorte, taux_presence, config, logo_reader, numero_certificat, None)

        c.showPage()
        count += 1

    if count == 0:
        c.drawString(2 * cm, 2 * cm, "Aucun certificat à générer.")
        c.showPage()

    c.save()
    buffer.seek(0)
    return buffer.getvalue()
