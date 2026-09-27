"""
Participating sites. There is one entry link for everyone; the site is
determined from the participant's email domain, and only emails from a
listed domain (or a subdomain of one, e.g. ifi.uzh.ch) are admitted.

The site key is stored with the participant's research record, so responses
can be split by site at analysis time. The site's study information is shown
on the consent page, once the site is known. The experiment itself is
identical for every site. Adding a site means adding an entry here.
"""

from .participants import email_matches_domain

SITES = {
    "uzh": {
        "name": "University of Zurich",
        "email_domains": ["uzh.ch"],
        "research_note": (
            "This research study is conducted by Ebrar Karadeniz in the Social "
            "Computing Group at the Department of Informatics, University of "
            "Zurich, under the supervision of Dr. Nicolò Pagan. If you have any "
            "questions, concerns, or technical issues while using this platform, "
            "please contact ebrarsevval.karadeniz@uzh.ch."
        ),
    },
    "ncsu": {
        "name": "North Carolina State University",
        "email_domains": ["ncsu.edu"],
        # TODO: replace with the IRB-approved study information for this site.
        "research_note": "TODO: NCSU study information and contact details.",
    },
}


def get_site(key):
    return SITES.get((key or "").lower())


def site_for_email(email):
    """Key of the site whose email domain matches, or None if none does."""
    for key, site in SITES.items():
        if any(email_matches_domain(email, d) for d in site["email_domains"]):
            return key
    return None


def allowed_email_domains():
    return [d for site in SITES.values() for d in site["email_domains"]]
