"""
adhesion/badges.py

Génération de badges d'accès professionnels en PDF (individuels et en planche A4 prête à imprimer)
avec QR code scannable haute résolution. Même palette navy/or que les certificats
(voir adhesion/certificats.py) pour que les deux documents se reconnaissent comme
venant du même émetteur.
"""

import io
import qrcode
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader

from .models import ConfigAssociation
from .utils import construire_contenu_carte

COULEUR_NAVY = colors.HexColor("#1a3a5c")
COULEUR_OR = colors.HexColor("#c9a84c")
COULEUR_FOND_BANDEAU = colors.HexColor("#0f2540")
COULEUR_TEXTE = colors.HexColor("#1f2937")
COULEUR_TEXTE_DOUX = colors.HexColor("#4b5563")


def _generer_qr_code(donnees: str, taille: int = 180) -> ImageReader:
    """Génère un QR code en mémoire et le retourne comme ImageReader."""
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=6,
        border=1,
    )
    qr.add_data(donnees)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")

    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)
    return ImageReader(buffer)


def _charger_logo_reader(config) -> ImageReader | None:
    """Charge le logo de l'association (Configuration Asso), si renseigné."""
    if not config.logo:
        return None
    try:
        return ImageReader(config.logo.path)
    except Exception:
        return None


def _charger_photo_reader(participant) -> ImageReader | None:
    """Charge la photo du participant, si elle existe sur le disque."""
    if not participant.photo:
        return None
    try:
        return ImageReader(participant.photo.path)
    except Exception:
        return None


def _dessiner_badge(c, x, y, largeur, hauteur, participant, cohorte, logo_reader=None, inclure_photo=True):
    """
    Dessine un badge individuel aux coordonnées (x, y) de taille (largeur, hauteur).
    Format standard porte-badge ~ 92mm x 58mm.

    Si `inclure_photo` est vrai et que le participant a une photo, elle est
    affichée à gauche du badge et le texte se décale d'autant ; sinon le
    texte occupe toute la largeur disponible, comme un badge sans photo.
    """
    c.saveState()

    photo_reader = _charger_photo_reader(participant) if inclure_photo else None
    marge = 4 * mm
    x_texte = x + marge

    # Fond blanc avec bordure arrondie
    c.setFillColor(colors.white)
    c.setStrokeColor(COULEUR_NAVY)
    c.setLineWidth(1.1)
    c.roundRect(x, y, largeur, hauteur, 3.5 * mm, fill=1, stroke=1)

    # Bandeau supérieur navy + liseré or
    bandeau_h = 13 * mm
    c.setFillColor(COULEUR_FOND_BANDEAU)
    c.roundRect(x, y + hauteur - bandeau_h, largeur, bandeau_h, 3.5 * mm, fill=1, stroke=0)
    c.rect(x, y + hauteur - bandeau_h, largeur, bandeau_h / 2, fill=1, stroke=0)
    c.setFillColor(COULEUR_OR)
    c.rect(x, y + hauteur - bandeau_h - 0.7 * mm, largeur, 0.7 * mm, fill=1, stroke=0)

    # Logo (si disponible) + intitulé, dans le bandeau
    texte_debut_bandeau_x = x + marge
    if logo_reader is not None:
        logo_h = bandeau_h - 3.5 * mm
        try:
            iw, ih = logo_reader.getSize()
            logo_w = logo_h * (iw / ih) if ih else logo_h
        except Exception:
            logo_w = logo_h
        logo_w = min(logo_w, 16 * mm)
        c.drawImage(
            logo_reader, x + 2.5 * mm, y + hauteur - bandeau_h + 1.7 * mm,
            width=logo_w, height=logo_h, mask="auto", preserveAspectRatio=True,
        )
        texte_debut_bandeau_x = x + 2.5 * mm + logo_w + 2.5 * mm

    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(texte_debut_bandeau_x, y + hauteur - 6 * mm, "BADGE OFFICIEL D'ACCÈS")
    c.setFont("Helvetica", 6.5)
    c.setFillColor(COULEUR_OR)
    c.drawString(texte_debut_bandeau_x, y + hauteur - 10 * mm, "ASSOCIATION PARCELLES ASSAINIES EN ACTION")

    # Photo du participant, si incluse
    y_corps_haut = y + hauteur - bandeau_h - 3 * mm
    if photo_reader is not None:
        photo_taille = 20 * mm
        photo_x = x_texte
        photo_y = y_corps_haut - photo_taille
        c.setStrokeColor(COULEUR_OR)
        c.setLineWidth(0.8)
        c.roundRect(photo_x - 0.5 * mm, photo_y - 0.5 * mm, photo_taille + 1 * mm, photo_taille + 1 * mm, 1 * mm, fill=0, stroke=1)
        c.saveState()
        p = c.beginPath()
        p.roundRect(photo_x, photo_y, photo_taille, photo_taille, 0.8 * mm)
        c.clipPath(p, stroke=0, fill=0)
        c.drawImage(
            photo_reader, photo_x, photo_y, width=photo_taille, height=photo_taille,
            mask="auto", preserveAspectRatio=True, anchor="c",
        )
        c.restoreState()
        x_texte = photo_x + photo_taille + 4 * mm

    # Nom et prénom : la police rétrécit pour tenir dans l'espace
    # disponible plutôt que de couper des lettres (un nom tronqué sans
    # ellipse peut se lire comme un nom différent).
    c.setFillColor(COULEUR_TEXTE)
    nom_complet = f"{participant.prenom} {participant.nom}".upper()
    largeur_dispo_texte = (x + largeur - 4 * mm - 26 * mm) - x_texte  # jusqu'au QR
    taille_nom = 12
    while taille_nom > 7.5 and c.stringWidth(nom_complet, "Helvetica-Bold", taille_nom) > largeur_dispo_texte:
        taille_nom -= 0.5
    c.setFont("Helvetica-Bold", taille_nom)
    c.drawString(x_texte, y_corps_haut - 5 * mm, nom_complet)

    # Numéro de badge
    c.setFillColor(COULEUR_NAVY)
    c.setFont("Helvetica-Bold", 8.5)
    badge_no = participant.numero_badge or f"BADGE-{participant.id_participant:06d}"
    c.drawString(x_texte, y_corps_haut - 10 * mm, f"N° {badge_no}")

    # Informations cohorte / formation
    c.setFillColor(COULEUR_TEXTE_DOUX)
    c.setFont("Helvetica", 7.5)
    formation_titre = cohorte.formation.titre if hasattr(cohorte, "formation") and cohorte.formation else "Formation"
    max_chars = 30 if photo_reader is None else 22
    if len(formation_titre) > max_chars:
        formation_titre = formation_titre[: max_chars - 1] + "…"
    c.drawString(x_texte, y_corps_haut - 15 * mm, formation_titre)
    c.drawString(x_texte, y_corps_haut - 19 * mm, f"Session {cohorte.code_cohorte}")

    date_str = f"{cohorte.date_debut:%d/%m/%Y} - {cohorte.date_fin:%d/%m/%Y}"
    c.setFont("Helvetica-Oblique", 7)
    c.drawString(x_texte, y_corps_haut - 23 * mm, date_str)

    # QR Code scannable à droite : même contenu signé (uuid.version.signature)
    # que celui vérifié au scan de présence, pour que le badge imprimé
    # fonctionne réellement au contrôle d'accès.
    qr_payload = construire_contenu_carte(str(participant.uuid))
    qr_img = _generer_qr_code(qr_payload, taille=150)
    qr_size = 20 * mm
    qr_x = x + largeur - qr_size - 4 * mm
    qr_y = y + 4 * mm
    c.setStrokeColor(COULEUR_OR)
    c.setLineWidth(0.6)
    c.rect(qr_x - 0.6 * mm, qr_y - 0.6 * mm, qr_size + 1.2 * mm, qr_size + 1.2 * mm, fill=0, stroke=1)
    c.drawImage(qr_img, qr_x, qr_y, width=qr_size, height=qr_size)

    # Ligne inférieure d'authenticité
    c.setFillColor(COULEUR_TEXTE_DOUX)
    c.setFont("Helvetica", 6)
    c.drawString(x_texte, y + 2.5 * mm, "Badge personnel et vérifiable par scan.")

    # Lignes de coupe (pointillés discrets)
    c.setStrokeColor(colors.HexColor("#cbd5e1"))
    c.setLineWidth(0.5)
    c.setDash(2, 2)
    c.rect(x - 2 * mm, y - 2 * mm, largeur + 4 * mm, hauteur + 4 * mm, stroke=1, fill=0)
    c.setDash()

    c.restoreState()


def generer_badge_unique_pdf(participant, cohorte, inclure_photo: bool = True) -> bytes:
    """Génère un PDF avec un seul badge centré sur une page format A6."""
    buffer = io.BytesIO()
    page_w, page_h = 105 * mm, 148 * mm
    c = canvas.Canvas(buffer, pagesize=(page_w, page_h))

    config = ConfigAssociation.charger()
    logo_reader = _charger_logo_reader(config)

    badge_w = 95 * mm
    badge_h = 60 * mm
    x = (page_w - badge_w) / 2
    y = (page_h - badge_h) / 2

    _dessiner_badge(c, x, y, badge_w, badge_h, participant, cohorte, logo_reader, inclure_photo)
    c.showPage()
    c.save()
    buffer.seek(0)
    return buffer.getvalue()


def generer_planche_badges_pdf(inscriptions_cohorte, inclure_photo: bool = True) -> bytes:
    """
    Génère une planche A4 (portrait) contenant 4 badges par page avec lignes de découpe.
    Parfait pour l'impression groupée sur papier cartonné ou étiquettes.
    """
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    page_w, page_h = A4  # 210 x 297 mm

    config = ConfigAssociation.charger()
    logo_reader = _charger_logo_reader(config)

    badge_w = 92 * mm
    badge_h = 58 * mm
    margin_x = 10 * mm
    margin_y = 15 * mm

    badge_idx = 0
    for insc in inscriptions_cohorte:
        p = insc.participant
        coh = insc.cohorte

        slot_on_page = badge_idx % 4
        if badge_idx > 0 and slot_on_page == 0:
            c.showPage()

        col = slot_on_page % 2
        row = slot_on_page // 2

        x = margin_x + col * (badge_w + 12 * mm)
        y = page_h - margin_y - (row + 1) * (badge_h + 16 * mm)

        _dessiner_badge(c, x, y, badge_w, badge_h, p, coh, logo_reader, inclure_photo)
        badge_idx += 1

    c.showPage()
    c.save()
    buffer.seek(0)
    return buffer.getvalue()
