import logging
import sys
from pathlib import Path
from typing import Dict, List, Tuple

logger = logging.getLogger(__name__)


def create_dashboard(
    metrics: Dict[str, List[Tuple[str, int]]],
    invalid_user_failures: int,
) -> None:
    try:
        import matplotlib.pyplot as plt
    except ImportError as error:
        logger.exception("Matplotlib is not installed")
        raise RuntimeError("Install the project dependencies to create the dashboard") from error

    figure, axes = plt.subplots(1, 3, figsize=(16, 6))
    charts = (
        (axes[0], metrics["failed_by_country"], "Échecs par pays", "Pays"),
        (axes[1], metrics["success_by_user"], "Réussites par utilisateur", "Utilisateur"),
    )
    for axis, values, title, label in charts:
        axis.set_title(title)
        axis.set_xlabel("Nombre de tentatives")
        if values:
            names, totals = zip(*values)
            axis.barh(names, totals, color="#4472C4")
            axis.invert_yaxis()
        else:
            axis.text(0.5, 0.5, "Aucune donnée", ha="center", va="center")
            axis.set_yticks([])
        axis.set_ylabel(label)

    invalid_axis = axes[2]
    invalid_axis.set_title("Échecs avec utilisateur invalide")
    invalid_axis.barh(["Invalid user"], [invalid_user_failures], color="#C0504D")
    invalid_axis.set_xlabel("Nombre de tentatives")
    invalid_axis.set_xlim(left=0)

    figure.suptitle("Analyse des tentatives d'authentification")
    figure.tight_layout()
    try:
        plt.show()
    finally:
        plt.close(figure)
    logger.info("Dashboard displayed")


def main() -> int:
    if __package__ in (None, ""):
        project_root = Path(__file__).resolve().parents[1]
        sys.path.insert(0, str(project_root))

    from storage.database import fetch_dashboard_metrics

    try:
        metrics, invalid_user_failures = fetch_dashboard_metrics()
        create_dashboard(metrics, invalid_user_failures)
    except (OSError, RuntimeError) as error:
        logging.basicConfig(level=logging.INFO)
        logger.exception("Could not display dashboard: %s", error)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
