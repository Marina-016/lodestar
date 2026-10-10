"""Source facts, not judgments of scientific importance or authority."""
from datetime import datetime, timezone
from dataclasses import replace

from lodestar import venue
from lodestar.tools.arxiv_search import RECENT_DAYS, time_window


def merge_sources(sources):
    """Merge repeated observations without replacing known facts with missing fields."""
    merged = {}
    for source in sources:
        url = source.get('url')
        if not isinstance(url, str):
            continue
        old = merged.setdefault(url, {})
        incoming = {k: v for k, v in source.items() if v is not None and v != ''}
        if ((old.get('publication_status') == 'publication_record_found' and incoming.get('publication_status') != 'publication_record_found')
                or (old.get('verified_at') and incoming.get('publication_status') in (None, 'not_checked'))):
            for key in ('publication_status', 'venue', 'venue_note', 'metadata_provider', 'external_ids',
                        'publication_evidence', 'record_url', 'is_published', 'lookup_note', 'verified_at', 'lookups'):
                incoming.pop(key, None)
        if old.get('read_depth') == 'full' and incoming.get('read_depth') != 'full':
            for key in ('content', 'read_depth', 'coverage', 'evidence_spans'):
                incoming.pop(key, None)
        old.update(incoming)
    return list(merged.values())


def assess(sources, now=None):
    now = now or datetime.now(timezone.utc)
    facts = []
    for source in merge_sources(sources):
        try:
            age = (now.date() - datetime.fromisoformat(source.get('date', '')[:10]).date()).days
        except (ValueError, TypeError):
            age = None
        facts.append({'url': source['url'], 'title': source.get('title'), 'date': source.get('date'),
            'date_basis': source.get('date_basis', 'unspecified'),
            'age_days': age, 'within_recent_window': 0 <= age <= RECENT_DAYS if age is not None else None,
            **({'publication_status': source.get('publication_status', 'not_checked'),
                'peer_review_status': source.get('peer_review_status', 'not_verified'),
                'publication_evidence': source.get('publication_evidence', 'Unknown: no publication lookup has established acceptance or rejection.')}
               if source.get('source_type') == 'paper' or 'arxiv.org/abs/' in source['url'] else {})})
    return {'reference_window': time_window(RECENT_DAYS, now),
            'scope_note': 'This reference window is informational; explicit user and query time windows take precedence.', 'sources': facts,
            'recent_count': sum(s['within_recent_window'] is True for s in facts),
            'publication_checks': {status: sum(s.get('publication_status') == status for s in facts)
                for status in ('not_checked', 'unresolved', 'preprint_record_only', 'publication_record_found')},
            'evidence_limits': 'Dates and publication metadata are observations, not a quality ranking. '
                'Missing publication metadata neither proves nonpublication nor disqualifies useful research.'}


def answer_context(context):
    """One evidence packet, not repeated transcripts of every abstract and old answer."""
    sources = merge_sources(context['candidates'] + context['papers'])
    operations = []
    for event in context['dialogue_events']:
        result = event['result']
        operations.append({'action': event['action'], 'params': event['params'],
            **{k: result[k] for k in ('error', 'query_results', 'effective_query', 'time_window',
                'failure_kind', 'recovery', 'skipped_queries') if k in result},
            **({'result': result} if event['action'] == 'project_context' else {})})
    return {'current_time': context['current_time'], 'sources': sources,
            'evidence_assessment': assess(sources), 'operations': operations,
            'conversation': context.get('history', []),
            'learning': context.get('learning', []),
            'conversation_note': 'Prior assistant replies are conversational context, not verified source evidence.'}


def publication_lookup_fresh(source, now=None):
    """A cached unsuccessful lookup must not become a permanent publication verdict."""
    try:
        timestamp = datetime.fromisoformat(source.get('verified_at', ''))
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        age = ((now or datetime.now(timezone.utc)) - timestamp).total_seconds()
        return 0 <= age <= 900
    except (ValueError, TypeError):
        return False


def verify_publication(ws, source):
    """Reuse venue lookup on one known paper; absence of a record stays unknown."""
    cfg = replace(ws.config, venue_enrich_limit=1, tool_timeout_s=min(ws.config.tool_timeout_s, 3))
    if not cfg.enrich_venues:
        return {'url': source['url'], 'publication_status': 'unresolved',
                'peer_review_status': 'not_verified',
                'publication_evidence': 'Unknown: publication lookup is disabled.'}
    try:
        identity = {k: source[k] for k in ('url', 'title', 'authors', 'dedup_key') if k in source}
        if not hasattr(ws, '_publication_cooldowns'):
            ws._publication_cooldowns = {}
        enriched, note = venue.enrich_papers_venues(cfg, [{**identity, 'source_type': 'paper'}],
                                                  retry=False, cooldowns=ws._publication_cooldowns)
    except Exception as exc:
        enriched, note = [{}], f'Publication lookup failed: {type(exc).__name__}'
    paper = enriched[0]
    provider = paper.get('venue_note')
    checked = cfg.enrich_venues and (bool(provider) or cfg.search_mode == 'mock') and not note.startswith('Publication lookup failed:')
    published = checked and paper.get('is_published') is True and paper.get('venue') not in (None, venue.PREPRINT_VENUE)
    status = ('publication_record_found' if published else
              'preprint_record_only' if checked and paper.get('venue') == venue.PREPRINT_VENUE else 'unresolved')
    record_url = paper.get('record_url')
    lookups = paper.get('venue_lookups', [])
    unavailable = bool(lookups) and all(l['status'] in ('unavailable', 'rate_limited') for l in lookups)
    evidence = (f"{provider or 'fixture'} returned a publication record: {paper.get('venue')}. This does not establish peer review."
                if published else 'Only a preprint record was found; a separate accepted version may exist.'
                if status == 'preprint_record_only' else 'Unknown: publication providers were unavailable; this is not evidence of nonpublication.'
                if unavailable else 'Unknown: lookup did not establish acceptance or rejection.')
    return {'url': source['url'], 'title': source.get('title'), 'date': source.get('date'),
            'date_basis': source.get('date_basis', 'unspecified'), 'publication_status': status,
            'venue': paper.get('venue'), 'external_ids': paper.get('external_ids', {}),
            'record_url': record_url, 'publication_evidence': evidence,
            'verified_at': datetime.now(timezone.utc).isoformat(),
            'lookups': lookups,
            **({'recovery': 'Publication status remains unknown. If important, search the exact title for an official '
                'publisher, proceedings or conference acceptance page, then read that primary source. '
                'Do not infer nonpublication from missing metadata.'} if not published else {}),
            'metadata_provider': provider, 'peer_review_status': 'not_verified', 'lookup_note': note,
            'limitations': 'Publication metadata is not an authority ranking or proof of peer review. '
                            'An unresolved lookup is not evidence that a paper is unpublished.'}
