import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..auth import get_current_user
from ..config import get_settings
from ..db.models import CalibrationResult, User
from ..db.session import get_db
from ..schemas import CalibrationResultOut

router = APIRouter(prefix="/projects/{project_id}/calibration", tags=["insights"])

_INSIGHTS_MODEL = "claude-sonnet-5"

_SYSTEM_PROMPT = (
    "You are assisting a lab scientist reviewing a sensor calibration run. "
    "You are given ONLY the computed fit statistics for one or more channels "
    "(never raw trace data). Write a short, plain-language assessment covering: "
    "(1) overall fit quality — is R² good, is the sensitivity reasonable; "
    "(2) how it compares to the relevant ideal (e.g. Nernstian ideal slope for "
    "solid-state sensors) or to other channels analyzed together, flagging any "
    "outlier channel; (3) concrete next-step suggestions (e.g. narrow an "
    "averaging window, add a standard in an underrepresented concentration "
    "range). Be specific and use the actual numbers given. Keep it under 200 words."
)


def _build_prompt(results: dict, fit_type: str) -> str:
    lines = [f"Fit type: {fit_type}", ""]
    for ch_name, res in results.items():
        lines.append(f"Channel: {ch_name}")
        if fit_type == "nernstian":
            seg = res.get("nernstian_segment") or {}
            lines.append(f"  Nernstian slope: {seg.get('slope')} mV/decade "
                         f"(ideal: {res.get('ideal_slope_mv_per_decade')})")
            lines.append(f"  % of ideal: {res.get('pct_of_ideal_nernstian')}")
            lines.append(f"  R²: {seg.get('r2')}")
            lines.append(f"  LOD: {res.get('lod_conc')}")
        else:
            lines.append(f"  Concentrations: {res.get('concs')}")
            lines.append(f"  ΔI: {res.get('delta_i')}")
            lines.append(f"  Blank noise (sigma): {res.get('sigma_bl')}")
        lines.append("")
    return "\n".join(lines)


@router.post("/{result_id}/insights", response_model=CalibrationResultOut)
def generate_insights(
    project_id: uuid.UUID,
    result_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> CalibrationResult:
    result = db.get(CalibrationResult, result_id)
    if result is None or result.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Calibration result not found.")

    settings = get_settings()
    if not settings.anthropic_api_key:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "AI Insights isn't configured — ANTHROPIC_API_KEY is not set.",
        )

    import anthropic

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    prompt = _build_prompt(result.results, result.fit_type.value)
    response = client.messages.create(
        model=_INSIGHTS_MODEL,
        max_tokens=500,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(block.text for block in response.content if block.type == "text")

    result.insights_text = text
    result.insights_model = _INSIGHTS_MODEL
    result.insights_generated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(result)
    return result
