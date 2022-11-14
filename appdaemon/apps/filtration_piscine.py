# Version du 30/05/2022
import time
from datetime import timedelta
from typing import Callable, NoReturn

import hassapi as hass


class UnknownModeError(KeyError):
    """
    Erreur: Mode de fontionnement inconnu.
    """
    pass


class FiltrationPiscine(hass.Hass):
    """
    Classe Appdeamon de gestion de la piscine.
    Cette classe gère le temps de filtration de la piscine selon plusieurs modes
    (été, hiver, marche forcée, arrêt forcée, ...).

    Version d'origine: https://github.com/remycrochon/home-assistant/blob/master/appdaemon/apps/filtration_piscine.py

    """
    # variables de classes : elles sont 'globales' à toutes les instances de la classe
    # logger instance for main_log (piscine_log)
    logger = None
    # intervale en minutes entre les executions
    intervale_minutes: int = 5

    # Flag de fin de la temporisation
    fin_tempo: bool = False

    # Cle temporisateur
    tempo = None

    # Durée temporisateur pompe (parametre "tempo_eau")
    duree_tempo: float = None

    # coeficients utilisées en duree_abaque():
    abaque_a: float = 0.00335
    abaque_b: float = -0.14953
    abaque_c: float = 2.43489
    abaque_d: float = -10.72859

    # Registre associant des noms de mode de fonctionnement a des fonctions
    registre_mode: dict = {}

    def initialize(self):
        """
        Initialise la classe, executé au démarrage de la classe.
        :return: NoReturn
        """
        self.logger = self.get_main_log()

        self.logger.info("Initialisation AppDaemon Filtration Piscine.")

        # initialisation de la temporisation avant recopie temperature
        self.fin_tempo = False
        # Arret de la pompe sur initalisation
        self.turn_off(self.args["cde_pompe"])

        # enregistre les modes de fonctionnement connus
        self.registrer_mode_action('Ete', self.mode_ete)
        self.registrer_mode_action('Hiver', self.mode_hiver)
        self.registrer_mode_action('At F', self.mode_at_force)
        self.registrer_mode_action('Ma F', self.mode_ma_force)

        # définie les actions à exécuter si changement dans variables
        self.listen_state(self.change_temp, self.args["temperature_eau"])
        self.listen_state(self.change_mode, self.args["mode_de_fonctionnement"])
        self.listen_state(self.change_coef, self.args["coef"])
        self.listen_state(self.ecretage_h_pivot, self.args["h_pivot"])
        self.listen_state(self.change_mode_calcul, self.args["mode_calcul"])
        self.listen_state(self.raz_temporisation_mesure_temp, self.args["cde_pompe"], new_state="off")
        self.listen_state(self.fin_temporisation_mesure_temp, self.args["cde_pompe"], new_state="on",
                          duration=float(self.get_state(self.args["tempo_eau"])))
        self.listen_state(self.change_tempo_circulation_eau, self.args["tempo_eau"])
        self.listen_state(self.change_arret_force, self.args["arret_force"])

        # Lance le traitement a chaque self.intervale_minutes (minutes)
        self.logger.debug(f'Ajout temporisateur pour exécuter le traitement toutes les {self.intervale_minutes} min.')
        self.run_every(self.traitement, "now", self.intervale_minutes * 60)

    def registrer_mode_action(self, mode_fonctionnement: str, action: Callable[[], bool]) -> NoReturn:
        """
        Cette méthode associe un nom de mode de fonctionnement à une fonction qui execute ce mode.
        :param mode_fonctionnement: str texte avec le nom du mode de fonctionnement.
        :param action: Callable[[], bool] une fonction qui execute ce mode de fonctionnement.
        :return: NoReturn
        """
        self.registre_mode[mode_fonctionnement] = action

    def get_mode_action(self, mode_fonctionnement: str) -> Callable[[], bool]:
        """
        Retourne l'action associée au mode de fonctionnement.
        Remonte une exception UnknownModeError si mode inconnu.
        :param mode_fonctionnement: str avec le libellée du mode de fonctionnement.
        :return: Callable[[], bool] l'action associée au mode de fonctionnement.
        """
        try:
            action: Callable[[], bool] = self.registre_mode[mode_fonctionnement]
            return action
        except KeyError:
            raise UnknownModeError(f'Mode de fonctionnement unconnu: {mode_fonctionnement}')

    @staticmethod
    def duree_classique(temperature_eau: float):
        """
        Fonction de calcul du temps de filtration "Classique":

           D = min(T / 2, 23)

        T est forcée a 10°C minimum.
        La durée maximale est plafonnée à 23 heures.
        :param temperature_eau: temperature de l'eau de la piscine.
        :return: la durée de filtration.
        """
        # Methode classique temperature / 2
        temperature_min: float = max(temperature_eau, 10)
        duree = temperature_min / 2
        duree_m = min(duree, 23)
        return duree_m

    def duree_abaque(self, temperature_eau: float) -> float:
        """
        Fonction de calcul de la durée de filtration selon un abacus.
        La formule:

           D = a*T^3 + b*T^2 + c*T + d

        T est forcée a 10°C minimum.
        Formule découverte dans: https://github.com/scadinot/pool
        Filtration en heures.

        :param temperature_eau: temperature de l'eau de la piscine.
        :return: float la durée de filtration calculée.
        """
        temperature_min: float = max(temperature_eau, 10)
        duree = (
                self.abaque_a * temperature_min ** 3
                + self.abaque_b * temperature_min ** 2
                + self.abaque_c * temperature_min
                + self.abaque_d
        )
        return duree

    def change_temp(self, entity, attribute, old, new, kwargs):
        """
        Méthode appelée sur changement de temperature.
        :param entity: entity affected.
        :param attribute: entity attribute.
        :param old: entity old value.
        :param new: entity new value.
        :param kwargs: remaining arguments.
        :return: NoReturn
        """
        if new != "unavailable":
            self.logger.debug('Appel traitement suite à un changement de température.')
            self.traitement(kwargs)

    def change_mode(self, entity, attribute, old, new, kwargs):
        """
        Méthode appelée sur changement de mode de fonctionnement.
        :param entity: entity affected.
        :param attribute: entity attribute.
        :param old: entity old value.
        :param new: entity new value.
        :param kwargs: remaining arguments.
        :return: NoReturn
        """
        self.fin_tempo = False
        self.logger.debug('Appel traitement changement Mode.')
        self.traitement(kwargs)

    def change_coef(self, entity, attribute, old, new, kwargs):
        """
        Méthode appelée sur changement de coefficient.
        :param entity: entity affected.
        :param attribute: entity attribute.
        :param old: entity old value.
        :param new: entity new value.
        :param kwargs: remaining arguments.
        :return: NoReturn
        """
        self.logger.debug('Appel traitement changement Coef.')
        self.traitement(kwargs)

    def change_mode_calcul(self, entity, attribute, old, new, kwargs):
        """
        Méthode appelée sur changement de mode de calcul.
        :param entity: entity affected.
        :param attribute: entity attribute.
        :param old: entity old value.
        :param new: entity new value.
        :param kwargs: remaining arguments.
        :return: NoReturn
        """
        self.logger.debug('Appel traitement changement mode de calcul.')
        self.traitement(kwargs)

    # Appelé sur changement arret forcé
    def change_arret_force(self, entity, attribute, old, new, kwargs):
        """
        Méthode appelée sur changement arrêt forcé.
        :param entity: entity affected.
        :param attribute: entity attribute.
        :param old: entity old value.
        :param new: entity new value.
        :param kwargs: remaining arguments.
        :return: NoReturn
        """
        self.logger.debug('Appel traitement sur arret force.')
        self.traitement(kwargs)

    def raz_temporisation_mesure_temp(self, kwargs):
        """
        Méthode appelée sur changement d'état de la pompe de filtrage de 'on' à 'off'.
        :param kwargs:
        :return:
        """
        self.fin_tempo = False
        self.logger.debug(f'Remise à zero temporisation circulation temp. Flag fin tempo: {self.fin_tempo}')

    def fin_temporisation_mesure_temp(self, kwargs):
        """
        Méthode appelée sur fin temporisation suite au démarrage de la pompe.
        :param kwargs: liste des arguments.
        :return:
        """
        self.fin_tempo = True
        self.logger.debug(f'Fin temporisation circulation eau. Flag fin tempo: {self.fin_tempo}')

    def ecretage_h_pivot(self, entity, attribute, old, new, kwargs):
        """
        Méthode appelée pour écrétage heure pivot entre h_pivot_min et h_pivot_max.
        :param entity: entity affected.
        :param attribute: entity attribute.
        :param old: entity old value.
        :param new: entity new value.
        :param kwargs: remaining arguments.
        :return: NoReturn
        """
        h_pivot = new
        h_pivot_max = "14:00:00"
        h_pivot_min = "11:00:00"
        if h_pivot > h_pivot_max:
            self.set_state(self.args["h_pivot"], state=h_pivot_max)
        if h_pivot < h_pivot_min:
            self.set_state(self.args["h_pivot"], state=h_pivot_min)
        self.traitement(kwargs)

    def change_tempo_circulation_eau(self, entity, attribute, old, new, kwargs):
        """
        Appelée sur changement temporisation circulation eau.
        :param entity:
        :param attribute:
        :param old:
        :param new:
        :param kwargs:
        :return:
        """
        self.fin_tempo = False
        self.logger.debug(f'Appel changement tempo circulation eau. Flag fin tempo: {self.fin_tempo}')
        self.initialize()

    def mode_ete(self) -> bool:
        """
        Action pour mode de fonctionnement 'Ete'.
        :return: bool fonctionnement de la pompe: True pour 'on', False pour 'off'.
        """
        mesure_temperature_eau = float(self.get_state(self.args["temperature_eau"]))
        mem_temperature_eau = float(self.get_state(self.args["mem_temp"]))
        coef = float(self.get_state(self.args["coef"])) / 100
        mode_calcul = self.get_state(self.args["mode_calcul"])
        periode_filtration = self.args["periode_filtration"]
        pivot = self.get_state(self.args["h_pivot"])

        # Flag FIN_TEMPO
        self.logger.debug(f"Flag fin tempo: {self.fin_tempo}")
        # Temporisation avant prise en compte de la mesure de la temperature
        self.logger.debug(f"Durée tempo recirculation: {float(self.get_state(self.args['tempo_eau']))}")
        # Sinon, on travaille avec la mémoire de la temperature avant arrêt de la pompe
        # mémorisée la veille.
        if self.fin_tempo:
            temperature_eau = mesure_temperature_eau
            self.set_value(self.args["mem_temp"], mesure_temperature_eau)
        else:
            temperature_eau = mem_temperature_eau

        temps_filtration = (self.duree_abaque(temperature_eau) if mode_calcul == 'on'
                            else self.duree_classique(temperature_eau)) * coef

        self.logger.debug(f"Température Eau: {temperature_eau} / Coéficient de filtration: {coef}")
        self.logger.debug(f"Durée de Filtration (Mode {'Abaque' if mode_calcul == 'on' else 'Classique'}): "
                          f"{temps_filtration} h")

        # conversion en timedelta pour simplifier la vie
        h_temps_filtration = timedelta(hours=temps_filtration)
        h_mi_temps_filtration = h_temps_filtration / 2

        # Calcul des heures de début et fin filtration en fonction
        # du temps de filtration avant et après l'heure pivot
        h_pivot = timedelta(hours=int(pivot[:2]), minutes=int(pivot[3:5]))

        h_debut = h_pivot - h_mi_temps_filtration
        # Limitation de la fin de filtration à 23:59:59
        h_max = timedelta(hours=23, minutes=59, seconds=59)
        h_fin = min(h_pivot + h_mi_temps_filtration, h_max)

        self.logger.info(f"Mi temps: {h_mi_temps_filtration}/Total Filtration: {h_temps_filtration}")
        self.logger.info(f"h_debut: {h_debut}/h_pivot: {h_pivot}/h_fin: {h_fin}")
        # Affichage plage horaire
        affichage_texte = f"{str(h_debut).zfill(8)[:5]}/{str(h_pivot).zfill(8)[:5]}/{str(h_fin).zfill(8)[:5]}"
        self.set_textvalue(periode_filtration, affichage_texte)
        # Ajouté le 30 mai 2022
        self.set_value("input_number.duree_filtration_ete", round(temps_filtration, 2))
        # fin ajout
        # Marche pompe si dans plage horaire sinon Arret
        marche_pompe: bool = self.now_is_between(str(h_debut), str(h_fin))
        return marche_pompe

    def mode_hiver(self) -> bool:
        """
        Action pour mode de fonctionnement 'Hiver'.
        :return: bool fonctionnement de la pompe: True pour 'on', False pour 'off'.
        """
        h_debut_h = self.get_state(self.args["h_debut_hiver"])
        h_debut_t = timedelta(hours=int(h_debut_h[:2]), minutes=int(h_debut_h[3:5]), seconds=int(h_debut_h[6:8]))

        duree = self.get_state(self.args["duree_hiver"])
        duree_t = timedelta(hours=duree)

        h_fin = h_debut_t + duree_t
        # Affichage plage horaire
        periode_filtration = self.args["periode_filtration"]
        affichage_texte = f"{str(h_debut_h).zfill(8)[:5]}/{str(h_fin).zfill(8)[:5]}"
        self.set_textvalue(periode_filtration, affichage_texte)

        self.logger.debug(f"h_debut_h: {h_debut_t}, Duree H: {duree_t}, H_fin: {h_fin}")
        # Marche pompe si dans plage horaire sinon Arret
        marche_pompe: bool = self.now_is_between(str(h_debut_h), str(h_fin))
        return marche_pompe

    def mode_at_force(self) -> bool:
        """
        Action pour mode de fonctionnement 'At F' (Arrêt Forcé).
        :return: bool False pour le flag marche_pompe.
        """
        periode_filtration = self.args["periode_filtration"]
        self.set_textvalue(periode_filtration, "At manuel")
        return False

    def mode_ma_force(self) -> bool:
        """
        Action pour mode de fonctionnement 'Ma F' (Marche Forcée).
        :return: bool True pour le flag marche_pompe.
        """
        periode_filtration = self.args["periode_filtration"]
        self.set_textvalue(periode_filtration, "Ma manuel")
        return True

    def traitement(self, kwargs):
        """
        Traitement de calcul de la température.
        :param kwargs: liste d'arguments de la fonction.
        :return: NoReturn.
        """
        self.logger.debug('Démarrage traitement...')
        # Calcul sortie commande pompe filtration
        # Arrêt pompe sur arrêt forcé
        arret_force = self.get_state(self.args["arret_force"])
        pompe = self.args["cde_pompe"]
        if arret_force == "on":
            self.turn_off(pompe)
            self.logger.info("Arrêt par Deléstage (Forcé)")
            periode_filtration = self.args["periode_filtration"]
            self.set_textvalue(periode_filtration, "At delestage")
        else:
            mode_de_fonctionnement: str = self.get_state(self.args["mode_de_fonctionnement"])
            self.logger.debug(f"Mode de fonctionnement: {mode_de_fonctionnement}")
            #  Recupération du mode de fonctionnement
            marche_pompe: bool = False
            try:
                action: Callable[[], bool] = self.get_mode_action(mode_de_fonctionnement)
                marche_pompe = action()
            except UnknownModeError:
                self.logger.warning(f"Mode de fonctionnement Piscine Inconnu: {mode_de_fonctionnement}")

            if marche_pompe:
                self.turn_on(pompe)
                self.logger.info("Marche Pompe")
            else:
                self.turn_off(pompe)
                self.logger.info("Arrêt Pompe")

        self.logger.debug('Fin traitement.')
