"""
Participating sites. Each site has its own entry link (/start/<key>), its
own start page, study information and consent page; the experiment itself
is identical for every site.

Sites with `collects_identity` (UF) ask for email, student ID, name and
class on the start page, accept only emails from `email_domain`, and use the
email to block repeat participation. Sites without it (UZH) collect no
personal data and rely on the browser cookie and the first-participation
question — see app.utils.participants.

The site key is stored with the participant's research record, so responses
can be split by site at analysis time. Adding a site means adding an entry
here — no route changes are needed.
"""

SITES = {
    "uzh": {
        "name": "University of Zurich",
        "consent_template": "consent.html",
        # UZH collects no personal data: no email, name or student ID.
        "start_template": "index.html",
        "collects_identity": False,
        "research_note": (
            "This research study is conducted by Ebrar Karadeniz in the Social "
            "Computing Group at the Department of Informatics, University of "
            "Zurich, under the supervision of Dr. Nicolò Pagan. If you have any "
            "questions, concerns, or technical issues while using this platform, "
            "please contact ebrarsevval.karadeniz@uzh.ch."
        ),
    },
    "uf": {
        "name": "University of Florida",
        "consent_template": "consent_uf.html",
        # UF keeps the original entry form from the main branch: email, UFID,
        # name and class. They are stored only in the `participants`
        # collection, never in research records or exports.
        "start_template": "start_uf.html",
        "collects_identity": True,
        "email_domain": "ufl.edu",
        "research_note": (
            "This is a research study from the Emerging Technologies in "
            "Education Group under the supervision of Dr. Neha Rani. IRB "
            "Protocol #ET00044243. If you have any questions, concerns, or "
            "issues while using this site, please contact w.pitts@ufl.edu."
        ),
    },
}


def get_site(key):
    return SITES.get((key or "").lower())
