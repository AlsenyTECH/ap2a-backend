"""
adhesion/permissions.py

Classes de permission Django REST Framework, une par rôle du système,
avec support des permissions granulaires pour les admins désignés.

Rappel : grâce à AuthentificationParJeton (étape précédente),
request.user contient un objet Compte (pas un User Django classique)
une fois la requête authentifiée.

Architecture des permissions admin :
- Le SUPER ADMIN (est_super_admin=True) a accès à TOUT, sans vérification
  dans la table PERMISSION_ADMIN.
- Un ADMIN DÉSIGNÉ (est_admin=True, est_super_admin=False) n'a accès qu'aux
  modules pour lesquels il possède une ligne dans PERMISSION_ADMIN.
- Les vues utilisent APermission("CODE") pour vérifier l'accès à un module.
"""

from rest_framework.permissions import BasePermission

from .models import PermissionAdmin


class EstAuthentifie(BasePermission):
    """Vérifie juste qu'un Compte valide est attaché à la requête."""

    def has_permission(self, request, view):
        return bool(request.user) and getattr(request.user, "is_authenticated", False)


class EstMembre(BasePermission):
    """
    L'auteur de la requête doit avoir une fiche Membre associée
    à son Compte. hasattr fonctionne ici grâce au related_name="membre"
    défini sur le OneToOneField dans models.py : Django crée
    automatiquement compte.membre si une ligne MEMBRE existe pour ce
    compte, et lève une exception si on essaie d'y accéder quand elle
    n'existe pas - hasattr capture proprement ce cas sans planter.
    """

    def has_permission(self, request, view):
        return EstAuthentifie().has_permission(request, view) and hasattr(
            request.user, "membre"
        )


class EstControleur(BasePermission):
    """L'auteur de la requête doit avoir une fiche Contrôleur associée."""

    def has_permission(self, request, view):
        return EstAuthentifie().has_permission(request, view) and hasattr(
            request.user, "controleur"
        )


class EstAdmin(BasePermission):
    """Le Compte doit avoir le drapeau est_admin activé."""

    def has_permission(self, request, view):
        return (
            EstAuthentifie().has_permission(request, view)
            and request.user.est_admin
        )


class EstSuperAdmin(BasePermission):
    """
    Réservé au super admin (fondateur). Utilisé pour les actions
    critiques : nommer/destituer des admins, attribuer des permissions.
    """

    def has_permission(self, request, view):
        return (
            EstAuthentifie().has_permission(request, view)
            and request.user.est_super_admin
        )


class EstAdminPrincipal(BasePermission):
    """
    Alias conservé pour compatibilité avec le code existant.
    Pointe désormais vers est_super_admin au lieu de déduire le statut
    de l'absence de compte_nommant.
    """

    def has_permission(self, request, view):
        return EstSuperAdmin().has_permission(request, view)


class PeutControler(BasePermission):
    """
    Autorise le contrôle d'accès (scan, vérification, confirmation
    d'entrée) à quiconque est CONTROLEUR **ou** ADMIN - décision prise
    ensemble : un admin (y compris l'admin principal) doit pouvoir
    scanner directement, sans avoir besoin d'une fiche Contrôleur
    séparée. Voir vue_confirmer_entree pour le provisionnement
    automatique d'une fiche Controleur la première fois qu'un admin
    scanne (nécessaire pour PARTICIPE.controleur_scan, qui référence
    toujours une fiche Controleur).
    """

    def has_permission(self, request, view):
        return (
            EstControleur().has_permission(request, view)
            or EstAdmin().has_permission(request, view)
        )


def compte_a_permission(compte, code_permission):
    """
    Vérifie si un compte admin a une permission spécifique.
    Le super admin a automatiquement TOUTES les permissions.
    Un admin désigné doit avoir une ligne dans PERMISSION_ADMIN.
    """
    if not compte.est_admin:
        return False
    if compte.est_super_admin:
        return True
    return PermissionAdmin.objects.filter(
        compte=compte, code_permission=code_permission
    ).exists()


def APermission(code_permission):
    """
    Factory de permission : retourne une classe de permission DRF
    qui vérifie qu'un admin a le code_permission demandé.

    Usage dans les vues :
        @permission_classes([APermission("GERER_MEMBRES")])
        def vue_liste_membres(request): ...

    Le super admin passe toujours. Un admin désigné doit avoir
    explicitement cette permission dans sa table PERMISSION_ADMIN.
    """

    class PermissionGranulaire(BasePermission):
        def has_permission(self, request, view):
            if not EstAuthentifie().has_permission(request, view):
                return False
            return compte_a_permission(request.user, code_permission)

    # Nom lisible dans les erreurs DRF
    PermissionGranulaire.__name__ = f"APermission_{code_permission}"
    PermissionGranulaire.__qualname__ = f"APermission_{code_permission}"
    return PermissionGranulaire



def APermissionUneParmi(*codes_permission):
    """
    Comme APermission, mais suffit d'UNE des permissions listées. Pour
    les données partagées entre modules (ex: la liste des membres sert
    aussi à cibler les invités d'un événement ou les participants d'une
    cohorte).
    """

    class PermissionGranulaireUneParmi(BasePermission):
        def has_permission(self, request, view):
            if not EstAuthentifie().has_permission(request, view):
                return False
            return any(compte_a_permission(request.user, code) for code in codes_permission)

    nom = "APermissionUneParmi_" + "_".join(codes_permission)
    PermissionGranulaireUneParmi.__name__ = nom
    PermissionGranulaireUneParmi.__qualname__ = nom
    return PermissionGranulaireUneParmi


def PermissionSelonMethode(lecture, ecriture):
    """
    Combine deux permissions selon la méthode HTTP : `lecture` pour
    GET/HEAD/OPTIONS, `ecriture` pour tout le reste. Pour une même
    route qui sert à la fois de liste (large) et de modification
    (restreinte).
    """

    class PermissionParMethode(BasePermission):
        def has_permission(self, request, view):
            classe = lecture if request.method in ("GET", "HEAD", "OPTIONS") else ecriture
            return classe().has_permission(request, view)

    return PermissionParMethode
