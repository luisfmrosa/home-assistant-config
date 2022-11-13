# Version du 30/05/2022
import time
from datetime import timedelta

import hassapi as hass

# Variables globales
# Saisir ici les memes modes que dans HA 
TAB_MODE = ["Ete", "Hiver", "At F", "Ma F"]

# TODO: remplacer en_heure (str) par des timedelta
# TODO: généraliser la gestion des modes de fonctionnement (une fonction pour chaque mode)


class FiltrationPiscine(hass.Hass):
    """
    Classe Appdeamon de gestion de la piscine.
    Cette classe gère le temps de filtration de la piscine selon plusieurs modes
    (été, hiver, marche forcée, arrêt forcée, ...).
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

    def initialize(self):
        """
        Initialise la classe, executé au démarrage de la classe.
        :return: NoReturn
        """
        self.logger = self.get_main_log()

        self.logger.info("Initialisation AppDaemon Filtration Piscine.")

        self.listen_state(self.change_temp, self.args["temperature_eau"])
        self.listen_state(self.change_mode, self.args["mode_de_fonctionnement"])
        self.listen_state(self.change_coef, self.args["coef"])
        self.listen_state(self.ecretage_h_pivot, self.args["h_pivot"])
        self.listen_state(self.change_mode_calcul, self.args["mode_calcul"])
        self.listen_state(self.change_etat_pompe, self.args["cde_pompe"])
        self.listen_state(self.change_arret_force, self.args["arret_force"])

        # Lance le traitement a chaque self.intervale_minutes (minutes)
        self.logger.debug(f'Ajout temporisateur pour exécuter le traitement toutes les {self.intervale_minutes} min.')
        self.run_every(self.traitement, "now", self.intervale_minutes * 60)

        # initialisation de la temporisation avant recopie temperature
        self.duree_tempo = float(self.get_state(self.args["tempo_eau"]))
        self.logger.info(f"Duree tempo: {self.duree_tempo}")
        self.tempo = self.run_in(self.fin_temporisation_mesure_temp, self.duree_tempo, entité=self.args["cde_pompe"])
        self.fin_tempo = False        # Arret de la pompe sur initalisation
        self.turn_off(self.args["cde_pompe"])

    @staticmethod
    def duree_classique(temperature_eau: str):
        """
        Fonction de calcul du temps de filtration "Classique":

           D = min(T / 2, 23)

        T est forcée a 10°C minimum.
        La durée maximale est plafonnée à 23 heures.
        :param temperature_eau: temperature de l'eau de la piscine.
        :return: la durée de filtration.
        """
        # Methode classique temperature / 2
        temperature_min: float = max(float(temperature_eau), 10)
        duree = temperature_min / 2
        duree_m = min(float(duree), 23)
        return duree_m

    def duree_abaque(self, temperature_eau: str) -> float:
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
        temperature_min: float = max(float(temperature_eau), 10)
        duree = (
                self.abaque_a * temperature_min ** 3
                + self.abaque_b * temperature_min ** 2
                + self.abaque_c * temperature_min
                + self.abaque_d
        )
        return duree

    @staticmethod
    def en_heure(t):
        """
        Fonction de Conversion d'heures (int) en heure "HH:MM:SS".
        :param t: un entier représentant l'heure.
        :return: l'heure en format "HH:MM:SS".
        """
        # TODO: etudier comment remplacer cette conversion par un simple timedelta()
        h = int(t)
        # On retire les heures pour ne garder que les minutes.
        t = (t - h) * 60  # 0.24 * 60 = temps_restant en minutes.
        m = int(t)
        # On retire les minutes pour ne garder que les secondes.
        t = (t - m) * 60
        s = int(t)
        return f"{h:02d}:{m:02d}:{s:02d}"

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
        self.notification('Appel traitement changement mode de calcul.', 2)
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

    def change_etat_pompe(self, entity, attribute, old, new, kwargs):
        """
        Méthode appelée sur changement d'état de la pompe de filtrage.
        :param entity: entity affected.
        :param attribute: entity attribute.
        :param old: entity old value.
        :param new: entity new value.
        :param kwargs: remaining arguments.
        :return: NoReturn
        """
        self.fin_tempo = False
        if new == "on":
            self.logger.debug(f"Etat pompe changé à 'on': appel à fin temporisation dans {self.duree_tempo}")
            self.tempo = self.run_in(self.fin_temporisation_mesure_temp, self.duree_tempo)
        else:
            self.logger.debug(f"Etat pompe changé à '{new}': arrêt temporisateur {self.tempo}")
            if self.tempo is not None:
                self.tempo = self.cancel_timer(self.tempo)
                self.logger.debug(f"self.tempo après arrêt tempo': {self.tempo}")

        # self.logger.debug('Appel traitement changement etat pompe.')
        # self.traitement(kwargs)

    def fin_temporisation_mesure_temp(self, kwargs):
        """
        Méthode appelée sur fin temporisation suite au démarrage de la pompe.
        :param kwargs: liste des arguments.
        :return:
        """
        self.fin_tempo = True
        self.logger.debug('Fin temporisation circulation eau.')
        # self.traitement(kwargs)

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

    def traitement(self, kwargs):
        """
        Traitement de calcul de la température.
        :param kwargs: liste d'arguments de la fonction.
        :return: NoReturn.
        """
        self.logger.debug('Démarrage traitement...')
        h_locale = time.strftime('%H:%M:%S', time.localtime())
        mesure_temperature_eau = float(self.get_state(self.args["temperature_eau"]))
        mem_temperature_eau = float(self.get_state(self.args["mem_temp"]))
        mode_de_fonctionnement = self.get_state(self.args["mode_de_fonctionnement"])
        arret_force = self.get_state(self.args["arret_force"])
        pompe = self.args["cde_pompe"]
        pivot = self.get_state(self.args["h_pivot"])
        coef = float(self.get_state(self.args["coef"])) / 100
        mode_calcul = self.get_state(self.args["mode_calcul"])
        periode_filtration = self.args["periode_filtration"]

        # Flag FIN_TEMPO
        self.logger.debug(f"Flag fin tempo = {self.fin_tempo}")
        # Temporisation avant prise en compte de la mesure de la temperature
        # sinon on travaille avec la memoire de la temperature avant arrêt de la pompe
        # mémorisée la veille.
        if self.fin_tempo:
            temperature_eau = mesure_temperature_eau
            self.set_value(self.args["mem_temp"], mesure_temperature_eau)
        else:
            temperature_eau = mem_temperature_eau

        #  Mode Ete
        if mode_de_fonctionnement == TAB_MODE[0]:

            if mode_calcul == "on":  # Calcul selon Abaque
                temps_filtration = (self.duree_abaque(temperature_eau)) * coef
                nb_h_avant = self.en_heure(float(temps_filtration / 2))
                nb_h_apres = self.en_heure(float(temps_filtration / 2))
                # nb_h_avant = en_heure(float(temps_filtration / 3)) Répartition 1/3-2/3
                # nb_h_apres = en_heure(float(temps_filtration / 3*2))
                nb_h_total = self.en_heure(float(temps_filtration))
                self.logger.debug(f"Durée de Filtration Mode Abaque: {temps_filtration} h")

            else:  # Calcul selon méthode classique
                temps_filtration = (self.duree_classique(temperature_eau)) * coef
                nb_h_avant = self.en_heure(float(temps_filtration / 2))
                nb_h_apres = self.en_heure(float(temps_filtration / 2))
                nb_h_total = self.en_heure(float(temps_filtration))
                self.logger.debug(f"Durée de Filtration Mode Classique: {temps_filtration} h")

            # Calcul des heures de début et fin filtration en fontion
            # du temps de filtration avant et apres l'heure pivot
            # Adapte l'heure de début de filtration à l'heure actuelle
            # Limitation de la fin de filtration à 23:59:59
            h_maintenant = timedelta(hours=int(h_locale[:2]), minutes=int(h_locale[3:5]), seconds=int(h_locale[6:8]))
            h_pivot = timedelta(hours=int(pivot[:2]), minutes=int(pivot[3:5]))
            h_avant_t = timedelta(hours=int(nb_h_avant[:2]), minutes=int(nb_h_avant[3:5]), seconds=int(nb_h_avant[6:8]))
            h_apres_t = timedelta(hours=int(nb_h_apres[:2]), minutes=int(nb_h_apres[3:5]), seconds=int(nb_h_apres[6:8]))
            h_total_t = timedelta(hours=int(nb_h_total[:2]), minutes=int(nb_h_total[3:5]), seconds=int(nb_h_total[6:8]))
            h_max_t = timedelta(hours=23, minutes=59, seconds=59)

            h_debut = h_pivot - h_avant_t
            h_fin = h_pivot + h_apres_t

            h_fin = min(h_fin, h_max_t)
            self.logger.info(f"Nb h_avant_t: {h_avant_t}/Nb h_apres_t: {h_apres_t}/Nb h_total_t: {h_total_t}")
            self.logger.info(f"h_debut: {h_debut}/h_pivot: {h_pivot}/h_fin: {h_fin}")
            # Affichage plage horaire
            affichage_texte = f"{str(h_debut)[:5]:0>8}/{str(h_pivot)[:5]:0>8}/{str(h_fin)[:5]:0>8}"
            self.set_textvalue(periode_filtration, affichage_texte)
            # Ajouté le 30 mai 2022
            self.set_value("input_number.duree_filtration_ete", round(temps_filtration, 2))
            # fin ajout
            # Marche pompe si dans plage horaire sinon Arret
            marche_pompe: bool = self.now_is_between(str(h_debut), str(h_fin))

            # Notifications de debug
            self.logger.debug(f"Mode de fonctionnement: {mode_de_fonctionnement}")
            self.logger.debug(f"Température Eau = {temperature_eau}")
            self.logger.debug(f"h_pivot = {pivot}")
            self.logger.debug(f"Coefficient = {coef}")

        #  Mode hiver Heure de Début + Une durée en h 
        elif mode_de_fonctionnement == TAB_MODE[1]:
            h_debut_h = self.get_state(self.args["h_debut_hiver"])
            duree = self.get_state(self.args["duree_hiver"])
            duree_h = self.en_heure(float(duree))
            h_debut_t = timedelta(hours=int(h_debut_h[:2]), minutes=int(h_debut_h[3:5]), seconds=int(h_debut_h[6:8]))
            duree_t = timedelta(hours=int(duree_h[:2]), minutes=int(duree_h[3:5]))
            h_fin_f = h_debut_t + duree_t
            # Affichage plage horaire
            affichage_texte = f"{str(h_debut_h)[:5]:0>8}/{str(h_fin_f)[:5]:0>8}"
            self.set_textvalue(periode_filtration, affichage_texte)

            self.logger.debug(f"h_debut_h: {h_debut_h}, Duree H: {duree_h}, H_fin: {h_fin_f}")
            # Marche pompe si dans plage horaire sinon Arret
            marche_pompe = self.now_is_between(str(h_debut_h), str(h_fin_f))

        # Mode Arret Forcé
        elif mode_de_fonctionnement == TAB_MODE[2]:
            marche_pompe = False
            self.set_textvalue(periode_filtration, "At manuel")

        # Mode Marche Forcée
        elif mode_de_fonctionnement == TAB_MODE[3]:
            marche_pompe = True
            self.set_textvalue(periode_filtration, "Ma manuel")

        # Mode Inconnu: revoir le contenu de Input_select.mode_de_fonctionnement
        else:
            self.logger.warning(f"Mode de fonctionnement Piscine Inconnu: {mode_de_fonctionnement}")

        # Calcul sortie commande pompe filtration
        # Arrêt pompe sur arrêt forcé
        if arret_force == "on":
            self.turn_off(pompe)
            self.logger.info("Arrêt par Deléstage (Forcé)")
            self.set_textvalue(periode_filtration, "At delestage")
        else:
            if marche_pompe:
                self.turn_on(pompe)
                self.logger.info("Marche Pompe")
            else:
                self.turn_off(pompe)
                self.logger.info("Arrêt Pompe")

        self.logger.debug('Fin traitement.')
