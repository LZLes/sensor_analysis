"""Optional local-Ollama AI Insights panel, shared by Amperometry and
Solid-State calibration results."""

import streamlit as st

try:
    import ollama as _ollama
    _OLLAMA_LIB_OK = True
except BaseException:
    # AI Insights should just stay unavailable if the optional `ollama`
    # package isn't installed — never crash the whole app over it.
    _OLLAMA_LIB_OK = False

SS = st.session_state


_INSIGHTS_SYSTEM_PROMPT = (
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


def _build_insights_prompt(results: dict, fit_type: str) -> str:
    """Builds the plain-text prompt sent to the model — only computed stats,
    never raw trace data. fit_type is 'Nernstian' for Solid-State, or
    'Linear'/'Segmented Linear' for Amperometry."""
    lines = [f"Fit type: {fit_type}", ""]
    for ch_name, res in results.items():
        lines.append(f"Channel: {ch_name}")
        if fit_type == "Nernstian":
            seg = res.get("nernstian_segment") or {}
            _unit = res.get("signal_unit", "mV")   # actual configured unit, not always mV
            lines.append(f"  Nernstian slope: {seg.get('slope')} {_unit}/decade "
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


def _generate_ai_insights_ollama(results: dict, fit_type: str, model: str) -> str:
    """Sends only the computed fit statistics (never raw trace data) to a
    local Ollama model and returns its plain-language assessment. Requires
    Ollama (https://ollama.com) running locally with `model` already pulled
    — raises if it can't be reached, so the caller can show a friendly
    error instead of a crash."""
    response = _ollama.chat(
        model=model,
        messages=[
            {"role": "system", "content": _INSIGHTS_SYSTEM_PROMPT},
            {"role": "user", "content": _build_insights_prompt(results, fit_type)},
        ],
    )
    return response["message"]["content"]


def _render_ai_insights_section(res_map: dict, fit_type: str, key_prefix: str) -> None:
    """Optional 'AI Insights' panel shown below a calibration's statistics
    table. Uses a local Ollama model instead of a paid API — no key, no
    cost, and nothing leaves this machine. Cached per result set (a simple
    hash of the stats) so it isn't regenerated on every rerun, only when
    the user asks."""
    with st.expander("🤖 AI Insights (local model via Ollama)", expanded=False):
        if not _OLLAMA_LIB_OK:
            st.info(
                "Not available in this environment — the `ollama` Python "
                "package isn't installed (`pip install ollama`)."
            )
            return
        st.caption(
            "Sends only the computed fit statistics shown above — never raw "
            "trace data — to a model running locally via "
            "[Ollama](https://ollama.com). No API key, no cost, nothing "
            "leaves this machine. One-time setup: install Ollama, then "
            "`ollama pull llama3.2` (or any other model you already have)."
        )
        _model = st.text_input(
            "Ollama model", value=SS.get("ollama_model", "llama3.2"),
            key=f"{key_prefix}_ollama_model",
        )
        SS["ollama_model"] = _model
        _cache_key = f"{key_prefix}_{fit_type}_{hash(str(res_map))}"
        if st.button("Generate insights", key=f"{key_prefix}_ai_insights_btn"):
            try:
                with st.spinner("Asking the local model..."):
                    _text = _generate_ai_insights_ollama(res_map, fit_type, _model)
                SS.setdefault("_ai_insights_cache", {})[_cache_key] = _text
            except Exception as exc:
                st.error(f"Couldn't reach Ollama — is it running? ({exc})")
        _cached = SS.get("_ai_insights_cache", {}).get(_cache_key)
        if _cached:
            st.markdown(_cached)
