"""Semantic resource matching shared by log-service and sensor checks."""

from typing import Any, Dict, Iterable, List, Optional, Tuple


def _value(resource: Any, *names: str, default=None):
    for name in names:
        if isinstance(resource, dict) and name in resource:
            return resource[name]
        if hasattr(resource, name):
            return getattr(resource, name)
    return default


def _strings(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if item is not None]
    return [str(value)]


def _entries_uri(service: Any) -> str:
    entries = _value(service, "entries", "Entries")
    return str(_value(entries, "odata_id", "@odata.id", default="") or "")


def _log_facilities(service: Any) -> List[str]:
    facilities = _strings(_value(service, "log_facilities", "LogFacilities"))
    filters = _value(service, "syslog_filters", "SyslogFilters", default=[]) or []
    for item in filters:
        facilities.extend(_strings(_value(item, "log_facilities", "LogFacilities")))
    return facilities


def select_log_service(services: Iterable[Any], requirement: Dict[str, Any]) \
        -> Tuple[Optional[Any], Dict[str, Any]]:
    """Select the strongest advertised LogService match and return its evidence.

    Structured Redfish properties outrank configured identifier/name fallbacks.
    Services without an Entries link are ineligible unless the profile explicitly
    disables ``require_entries``.
    """
    required_purposes = {str(v).lower() for v in requirement.get("purposes", [])}
    required_facilities = {str(v).lower() for v in requirement.get("facilities", [])}
    preferred_ids = {str(v).lower() for v in requirement.get("preferred_ids", [])}
    keywords = [str(v).lower() for v in requirement.get("fallback_keywords", [])]
    require_entries = requirement.get("require_entries", True)
    candidates = []

    for index, service in enumerate(services):
        service_id = str(_value(service, "id", "Id", default="") or "")
        name = str(_value(service, "name", "Name", default="") or "")
        description = str(_value(service, "description", "Description", default="") or "")
        oem_purpose = str(_value(service, "oem_log_purpose", "OEMLogPurpose", default="") or "")
        purposes = _strings(_value(service, "log_purposes", "LogPurposes"))
        facilities = _log_facilities(service)
        entry_type = str(_value(service, "log_entry_type", "LogEntryType", default="") or "")
        service_enabled = _value(service, "service_enabled", "ServiceEnabled")
        entries_uri = _entries_uri(service)
        purpose_lookup = {value.lower(): value for value in purposes}
        facility_lookup = {value.lower(): value for value in facilities}
        purpose_matches = required_purposes & set(purpose_lookup)
        purpose_conflict = bool(required_purposes and purposes and not purpose_matches)
        text = " ".join((service_id, name, description, oem_purpose)).lower()
        matched_by = []
        score = 0

        for purpose in sorted(purpose_matches):
            matched_by.append(f"LogPurposes:{purpose_lookup[purpose]}")
            score += 100
        for facility in sorted(required_facilities & set(facility_lookup)):
            matched_by.append(f"LogFacilities:{facility_lookup[facility]}")
            score += 80
        if not purpose_conflict and service_id.lower() in preferred_ids:
            matched_by.append(f"preferred_id:{service_id}")
            score += 60
        if not matched_by and not purpose_conflict:
            for keyword in keywords:
                if keyword and keyword in text:
                    matched_by.append(f"keyword:{keyword}")
                    score += 20

        eligible = (bool(matched_by)
                    and service_enabled is not False
                    and (bool(entries_uri) or not require_entries))
        evidence = {
            "id": service_id,
            "name": name,
            "description": description,
            "log_purposes": purposes,
            "log_facilities": facilities,
            "log_entry_type": entry_type,
            "service_enabled": service_enabled,
            "entries_uri": entries_uri,
            "matched_by": matched_by,
            "purpose_conflict": purpose_conflict,
            "eligible": eligible,
            "score": score,
            "index": index,
        }
        candidates.append((service, evidence))

    eligible = [(service, evidence) for service, evidence in candidates
                if evidence["eligible"]]
    eligible.sort(key=lambda item: (-item[1]["score"], item[1]["id"], item[1]["index"]))
    selected_service, selected_evidence = eligible[0] if eligible else (None, None)
    return selected_service, {
        "requirement": requirement,
        "selected": selected_evidence,
        "candidates": [evidence for _, evidence in candidates],
    }


def _sensor_match_evidence(sensor: Dict[str, Any], rule: Dict[str, Any]) -> Dict[str, Any]:
    name = str(sensor.get("name") or "")
    context = str(sensor.get("physical_context") or "")
    subcontext = str(sensor.get("physical_sub_context") or "")
    related_items = _strings(sensor.get("related_items"))
    contexts = {str(v).lower() for v in rule.get("physical_contexts", [])}
    subcontexts = {str(v).lower() for v in rule.get("physical_sub_contexts", [])}
    related_keywords = [(str(v), str(v).lower())
                        for v in rule.get("related_item_keywords", [])]
    keywords = [str(v).lower() for v in rule.get("keywords", [])]
    matched_by = []
    has_selectors = bool(contexts or subcontexts or related_keywords or keywords)

    if not has_selectors:
        matched_by.append("source:any")

    if has_selectors and context.lower() in contexts:
        matched_by.append(f"PhysicalContext:{context}")
    if subcontext.lower() in subcontexts:
        matched_by.append(f"PhysicalSubContext:{subcontext}")
    if not matched_by:
        for configured, keyword in related_keywords:
            if any(keyword in uri.lower() for uri in related_items):
                matched_by.append(f"RelatedItem:{configured}")
    if not matched_by:
        for keyword in keywords:
            if keyword and keyword in name.lower():
                matched_by.append(f"keyword:{keyword}")

    return {
        "name": name,
        "physical_context": context,
        "physical_sub_context": subcontext,
        "related_items": related_items,
        "matched_by": matched_by,
    }


def evaluate_sensor_group(sensors: Iterable[Dict[str, Any]], rule: Dict[str, Any],
                          hardware_present: Optional[bool] = None) -> Dict[str, Any]:
    """Evaluate one profile sensor group with deterministic optionality rules."""
    requirement = rule.get("requirement", "required")
    evidence = [_sensor_match_evidence(sensor, rule) for sensor in sensors]
    matches = [item for item in evidence if item["matched_by"]]

    if matches:
        result, reason = "PASS", "matched"
    elif requirement == "optional":
        result, reason = "SKIPPED", "optional_not_advertised"
    elif requirement == "when_hardware_present" and hardware_present is False:
        result, reason = "SKIPPED", "hardware_absent"
    elif requirement == "when_hardware_present" and hardware_present is None:
        result, reason = "SKIPPED", "hardware_presence_unknown"
    else:
        result, reason = "FAIL", "missing_reading"

    return {
        "requirement": requirement,
        "hardware_present": hardware_present,
        "matches": matches,
        "candidates": evidence,
        "result": result,
        "reason": reason,
    }
