"""Apply an explicitly requested user admission without rewriting live health."""
from __future__ import annotations
import argparse,copy,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from sgar_mvp.scripts import check_model_health as health
from sgar_mvp.src.model_admission import approve_record
from sgar_mvp.src.model_selection import validated_candidate_admitted_ids
from sgar_mvp.src.pipeline_control import canonical_sha256

def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resource-id",required=True)
    parser.add_argument("--reason",required=True)
    parser.add_argument("--expected-health-sha256",required=True)
    args=parser.parse_args()
    path=health.DEFAULT_READY_STATE
    before=health._file_sha(path);payload=health.load_json(path)
    if payload.get("health_sha256")!=args.expected_health_sha256:
        raise ValueError("operator_admission_health_changed")
    validated_candidate_admitted_ids(payload,root=ROOT)
    candidate=copy.deepcopy(payload)
    matches=[r for r in candidate["models"] if r["resource_id"]==args.resource_id]
    if len(matches)!=1:raise ValueError("operator_admission_candidate_missing")
    approved=approve_record(matches[0],endpoint_sha256=candidate["endpoint_identity_sha256"],approved_at=health.utc_now(),reason=args.reason)
    candidate["models"]=[approved if r["resource_id"]==args.resource_id else r for r in candidate["models"]]
    candidate.pop("health_sha256",None);candidate["health_sha256"]=canonical_sha256(candidate)
    admitted=validated_candidate_admitted_ids(candidate,root=ROOT)
    health._write_ready_state(path,candidate,expected_sha=before)
    print(json.dumps({"health_sha256":candidate["health_sha256"],"admitted_model_count":len(admitted),"observed_health_unchanged":True}))
    return 0
if __name__=="__main__":raise SystemExit(main())
