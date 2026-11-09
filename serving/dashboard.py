import logging
import sys
from collections import Counter
from pathlib import Path
from typing import Dict, List

logger = logging.getLogger(__name__)

LogRecord = Dict[str, object]
LOGS_PER_PAGE = 18


def _label(record: LogRecord, key: str, fallback: str) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not value.strip():
        return fallback
    return value.strip()


def _display_value(value: object) -> str:
    if value is None:
        return "-"
    return str(value)


def create_dashboard(records: List[LogRecord]) -> None:
    try:
        import matplotlib.pyplot as plt
        from matplotlib.patches import FancyBboxPatch
        from matplotlib.widgets import Button, Slider
    except ImportError as error:
        logger.exception("Matplotlib is not installed")
        raise RuntimeError(
            "Install the project dependencies to create the dashboard"
        ) from error

    failures = [record for record in records if record.get("status") == "failed"]
    successes = [record for record in records if record.get("status") == "success"]
    invalid_failures = [
        record for record in failures if record.get("is_invalid_user") is True
    ]

    failures_by_source: Counter[str] = Counter(
        _label(record, "country", "")
        if record.get("country")
        else _label(record, "source", "Inconnu")
        for record in failures
    )
    successes_by_user: Counter[str] = Counter(
        _label(record, "username", "Inconnu") for record in successes
    )
    invalid_users: Counter[str] = Counter(
        _label(record, "username", "Inconnu") for record in invalid_failures
    )

    figure = plt.figure(figsize=(16, 10), facecolor="#f4f7fb")
    figure.text(
        0.055,
        0.965,
        "Log Analyser",
        fontsize=25,
        fontweight="bold",
        color="#172b4d",
        va="top",
    )
    figure.text(
        0.055,
        0.922,
        "Analyse complète des fichiers présents dans data/",
        fontsize=12,
        color="#64748b",
        va="top",
    )

    kpis = (
        ("Lignes de logs", len(records), "#315b8a"),
        ("Lignes d'échec SSH", len(failures), "#c2413b"),
        ("Connexions réussies", len(successes), "#16845b"),
        ("Échecs avec utilisateur invalide", len(invalid_failures), "#b7791f"),
    )
    card_width = 0.215
    for index, (label, total, accent) in enumerate(kpis):
        x_position = 0.055 + index * 0.227
        card = figure.add_axes([x_position, 0.805, card_width, 0.09])
        card.set_axis_off()
        card.add_patch(
            FancyBboxPatch(
                (0, 0),
                1,
                1,
                boxstyle="round,pad=0.012,rounding_size=0.06",
                facecolor="#ffffff",
                edgecolor="#e2e8f0",
                linewidth=1,
                transform=card.transAxes,
                clip_on=False,
            )
        )
        card.text(0.06, 0.68, label, fontsize=9.5, color="#64748b", va="center")
        card.text(
            0.06,
            0.28,
            f"{total:,}",
            fontsize=21,
            fontweight="bold",
            color=accent,
            va="center",
        )

    charts = (
        (
            0.055,
            "Échecs par pays / IP source",
            failures_by_source,
            "#d95d59",
            "Aucune ligne d'échec SSH détectée",
        ),
        (
            0.365,
            "Connexions réussies par utilisateur",
            successes_by_user,
            "#3387c9",
            "Aucune connexion réussie détectée",
        ),
        (
            0.675,
            "Utilisateurs invalides les plus utilisés",
            invalid_users,
            "#d49a36",
            "Aucun échec avec utilisateur invalide",
        ),
    )
    for x_position, title, values, color, empty_message in charts:
        axis = figure.add_axes([x_position, 0.455, 0.27, 0.295])
        axis.set_facecolor("#ffffff")
        axis.set_title(
            title,
            loc="left",
            fontsize=10,
            fontweight="bold",
            pad=10,
            color="#243b5a",
        )
        axis.grid(axis="x", color="#e7edf5", linewidth=0.8)
        axis.set_axisbelow(True)
        for spine in axis.spines.values():
            spine.set_visible(False)
        axis.tick_params(axis="both", colors="#64748b", labelsize=7.5, length=0)
        axis.set_xlabel("Nombre de lignes", color="#64748b", fontsize=8, labelpad=5)

        if values:
            ordered_values = values.most_common(8)
            labels, totals = zip(*ordered_values)
            axis.barh(labels, totals, color=color, height=0.62, zorder=3)
            axis.invert_yaxis()
            axis.set_xlim(left=0)
        else:
            axis.set_xticks([])
            axis.set_yticks([])
            axis.text(
                0.5,
                0.5,
                empty_message,
                ha="center",
                va="center",
                color="#94a3b8",
                fontsize=9,
                wrap=True,
                transform=axis.transAxes,
            )

    logs_axis = figure.add_axes([0.055, 0.08, 0.89, 0.34])
    logs_axis.set_facecolor("#ffffff")
    logs_axis.set_xticks([])
    logs_axis.set_yticks([])
    for spine in logs_axis.spines.values():
        spine.set_color("#e2e8f0")
    logs_axis.text(
        0.015,
        0.96,
        "Tous les logs — navigation par pages",
        fontsize=11,
        fontweight="bold",
        color="#243b5a",
        va="top",
        transform=logs_axis.transAxes,
    )
    total_pages = (len(records) + LOGS_PER_PAGE - 1) // LOGS_PER_PAGE
    page_label = logs_axis.text(
        0.985,
        0.96,
        "",
        fontsize=9,
        color="#64748b",
        ha="right",
        va="top",
        transform=logs_axis.transAxes,
    )
    logs_text = logs_axis.text(
        0.015,
        0.86,
        "",
        fontsize=7.2,
        color="#334155",
        family="monospace",
        va="top",
        linespacing=1.3,
        transform=logs_axis.transAxes,
    )

    previous_axis = figure.add_axes([0.055, 0.022, 0.09, 0.04])
    next_axis = figure.add_axes([0.855, 0.022, 0.09, 0.04])
    previous_button = Button(previous_axis, "Précédent")
    next_button = Button(next_axis, "Suivant")
    max_page = max(total_pages - 1, 1)
    slider_axis = figure.add_axes([0.2, 0.03, 0.62, 0.02])
    page_slider = Slider(
        slider_axis,
        "Page",
        0,
        max_page,
        valinit=0,
        valstep=1,
        valfmt="%0.0f",
    )
    current_page = [0]

    def render_page(page: int) -> None:
        if total_pages:
            current_page[0] = min(max(page, 0), total_pages - 1)
            start = current_page[0] * LOGS_PER_PAGE
            page_records = records[start : start + LOGS_PER_PAGE]
            lines = []
            for record in page_records:
                source_file = _display_value(record.get("source_file"))
                line_number = _display_value(record.get("line_number"))
                message = _display_value(record.get("message"))
                lines.append(f"{source_file}:{line_number} | {message}")
            logs_text.set_text("\n".join(lines))
            page_label.set_text(
                f"Page {current_page[0] + 1:,} / {total_pages:,}"
            )
        else:
            current_page[0] = 0
            logs_text.set_text("Aucun fichier de log lisible trouvé dans data/.")
            page_label.set_text("Page 0 / 0")
        figure.canvas.draw_idle()

    def change_page(amount: int) -> None:
        page_slider.set_val(current_page[0] + amount)

    previous_button.on_clicked(lambda _event: change_page(-1))
    next_button.on_clicked(lambda _event: change_page(1))
    page_slider.on_changed(lambda value: render_page(int(value)))
    figure.canvas.mpl_connect(
        "key_press_event",
        lambda event: (
            change_page(-1)
            if event.key == "left"
            else change_page(1)
            if event.key == "right"
            else None
        ),
    )
    render_page(0)

    try:
        plt.show()
    finally:
        plt.close(figure)
    logger.info("Dashboard displayed with %d log line(s)", len(records))


def main() -> int:
    if __package__ in (None, ""):
        project_root = Path(__file__).resolve().parents[1]
        sys.path.insert(0, str(project_root))

    from ingestion.raw import load_log_records

    try:
        project_root = Path(__file__).resolve().parents[1]
        records = load_log_records(project_root / "data")
        create_dashboard(records)
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        logging.basicConfig(level=logging.INFO)
        logger.exception("Could not display dashboard: %s", error)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
