# Analyseur de logs d'authentification

J'ai réalisé ce projet pour lire des journaux d'authentification, enregistrer les événements dans PostgreSQL et produire un tableau de bord des tentatives de connexion. Le pipeline utilise les modules de la bibliothèque standard de Python pour la lecture, la validation, les journaux applicatifs et la communication avec PostgreSQL. Matplotlib est la seule dépendance Python externe.

## Indicateurs du tableau de bord

Le dashboard est une application graphique locale Matplotlib et présente :

1. le nombre de tentatives échouées par pays ;
2. le nombre de tentatives réussies par utilisateur ;
3. le nombre total d'échecs associés à un utilisateur invalide.

Les indicateurs sont calculés à partir des données PostgreSQL, et non uniquement à partir du fichier actuellement importé.

## Format des journaux

Chaque ligne doit être un objet JSON. Les champs obligatoires sont `timestamp` (date ISO 8601) et `status` (`success` ou `failed`). `username` et `country` peuvent être absents ou nuls ; un pays absent est enregistré comme `Unknown`. Pour compter un échec avec un utilisateur invalide, le champ `reason` doit être `invalid_user` (sans tenir compte de la casse).

Exemple de fichier `data\auth.jsonl` :

```json
{"timestamp":"2026-10-25T10:15:00Z","username":"amina","country":"France","status":"success"}
{"timestamp":"2026-10-25T10:17:00Z","username":"inconnu","country":"Maroc","status":"failed","reason":"invalid_user"}
{"timestamp":"2026-10-25T10:20:00Z","username":"amina","country":"France","status":"failed","reason":"wrong_password"}
```

Le pipeline lit les fichiers `.jsonl`, `.log` et `.txt`, ainsi que leurs versions compressées `.gz`. Les lignes vides sont ignorées ; les événements mal formés sont ignorés et consignés dans les logs applicatifs.

## Étapes d'installation et d'exécution sous Windows

1. **Installer PostgreSQL** depuis le site officiel et démarrer son service. Pendant l'installation, définir le mot de passe de l'utilisateur PostgreSQL.
2. **Vérifier que `psql` est disponible** dans le terminal. Ajouter le dossier `bin` de PostgreSQL au `PATH` Windows si la commande `psql --version` n'est pas reconnue. Aucun pilote PostgreSQL Python n'est utilisé.
3. **Créer la base de données** depuis un terminal :

   ```powershell
   createdb -U postgres log_analyser
   ```

   On peut aussi créer la base dans pgAdmin. Le compte utilisé par l'application doit pouvoir créer les tables et index dans cette base.
4. **Installer Python 3.9 ou une version plus récente** et créer un environnement virtuel depuis le dossier du projet :

   ```powershell
   py -m venv .venv
   .venv\Scripts\Activate.ps1
   python -m pip install -r requirements.txt
   ```

5. **Configurer la connexion PostgreSQL** dans le terminal PowerShell qui exécutera le programme. Remplacer les exemples par vos paramètres :

   ```powershell
   $env:PGHOST = "localhost"
   $env:PGPORT = "5432"
   $env:PGUSER = "postgres"
   $env:PGPASSWORD = "votre_mot_de_passe"
   $env:PGDATABASE = "log_analyser"
   ```

   Si Python ne trouve pas `psql` via le `PATH`, indiquer directement son chemin :

   ```powershell
   $env:PSQL_PATH = "C:\Program Files\PostgreSQL\18\bin\psql.exe"
   ```

   Le mot de passe est transmis à `psql` via son environnement et n'est pas écrit dans le code ou les logs. Ne pas enregistrer un vrai mot de passe dans le dépôt.
6. **Placer les fichiers de logs** dans `data\`, puis lancer le pipeline :

   ```powershell
   python main.py
   ```

   Pour préciser un autre dossier ou un seul fichier :

   ```powershell
   python main.py --input "C:\chemin\vers\mes-logs"
   python main.py --input "C:\chemin\vers\auth.jsonl"
   ```

7. **Consulter le dashboard** dans la fenêtre Matplotlib ouverte par le pipeline. Pour rouvrir uniquement le dashboard après avoir importé des événements, lancer depuis la racine du projet :

   ```powershell
   python -m serving.dashboard
   ```

   Cette commande lit les indicateurs existants dans PostgreSQL ; elle ne réimporte pas les logs et ne crée pas de fichier PNG. Les erreurs ou événements ignorés restent consultables dans `logs\app.log`.

## Stockage et relance du pipeline

Le schéma est défini dans `storage\schema.sql` et créé automatiquement au démarrage. La table `auth_events` contient l'horodatage, l'utilisateur, le pays, le statut, le motif et la provenance (fichier et numéro de ligne). Les événements sont identifiés de façon unique par fichier et numéro de ligne : relancer le programme ne crée pas de doublons et met à jour un événement si la même ligne a changé.

Le programme interroge PostgreSQL avec le client officiel `psql`. Les erreurs d'exécution de ce client sont affichées et détaillées dans `logs\app.log`.

## Structure du projet

```text
Log_analyser/
|-- ingestion/raw.py       # Lecture et validation des lignes JSON
|-- storage/schema.sql     # Schéma et index PostgreSQL
|-- storage/database.py    # Import des événements et requêtes des indicateurs
|-- serving/dashboard.py   # Génération du graphique avec Matplotlib
|-- main.py                # Point d'entrée et configuration des logs
|-- requirements.txt       # Dépendances Python externes
```

Les logs applicatifs sont horodatés, indiquent le nom du module et sont conservés dans `logs\app.log` avec rotation pour limiter leur taille.
