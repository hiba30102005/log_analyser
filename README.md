# Analyseur de logs

Ce projet lit et valide des journaux LOG, TXT et ZIP, puis enregistre les
événements dans PostgreSQL.

Il analyse les échecs par pays, les connexions réussies par utilisateur et les
échecs avec utilisateur invalide. Le tableau de bord interactif est affiché
dans une fenêtre graphique avec Matplotlib.

Pour importer les logs et afficher le dashboard, lancez :

```powershell
python main.py
```
