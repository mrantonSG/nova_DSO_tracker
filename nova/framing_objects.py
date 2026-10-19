"""
nova/framing_objects.py - the graph page's Framing Assistant object list.

build_framing_objects() is the single definition of the rows the graph page
inlined as NOVA_GRAPH_DATA.allObjects until diff 2 and that /api/framing_objects
serves from then on: core.graph_dashboard() called it before, the API route
calls it after, so the two can never drift. Moved verbatim from
graph_dashboard() - same query, same fields, same values, same order.

Safe to import from any blueprint: it depends only on nova.models, which is
below nova.blueprints.* in the import chain (nova.models -> nova.config ->
nova.helpers -> nova.workers.* -> nova.init -> nova.blueprints.*).
"""
from nova.models import AstroObject


def build_framing_objects(db, user):
    """Every object of `user` that has RA and Dec - the Aladin "Nova objects"
    catalog layer. ra_deg/dec_deg/object_name/common_name/id place the marker and
    its label; the remaining curation fields feed the Inspiration modal that the
    marker's click handler opens (static/js/graph_view_chart.js,
    addNovaObjectsCatalog).
    """
    all_objects_for_framing = []
    try:
        all_objs = db.query(AstroObject).filter_by(user_id=user.id).filter(AstroObject.ra_hours != None,
                                                                           AstroObject.dec_deg != None).all()
        for o in all_objs:
            try:
                all_objects_for_framing.append({
                    "id": o.id, "object_name": o.object_name, "common_name": o.common_name,
                    "ra_deg": float(o.ra_hours) * 15.0, "dec_deg": float(o.dec_deg),
                    # Curation / Metadata fields required for Inspiration Modal
                    "image_url": o.image_url,
                    "image_credit": o.image_credit,
                    "image_source_link": o.image_source_link,
                    "description_text": o.description_text,
                    "description_credit": o.description_credit,
                    "description_source_link": o.description_source_link,
                    "Size": o.size,
                    # Note: Capitalized to match some frontend expectations if needed, or consistent with DB
                    "Type": o.type,
                    "Constellation": o.constellation,
                    # Essential keys for the frontend modal title/subtitle
                    "Object": o.object_name,
                    "Common Name": o.common_name
                })
            except Exception:
                continue
    except Exception:
        all_objects_for_framing = []
    return all_objects_for_framing
