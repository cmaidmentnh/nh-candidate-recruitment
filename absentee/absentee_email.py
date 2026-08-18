#!/usr/bin/env python3
"""Absentee-list email for R House candidates. Same shell as walkbook_welcome.py:
600px table, inline styles, real plain-text alternative."""

BRAND = "#b30000"
INK   = "#111111"
MUTED = "#5a5a5a"
RULE  = "#dcdcdc"
LOGO  = "https://actioncenter.winthehouse.gop/uploads/5ad750845a964185980993b6fcba77e6.png"
ORG   = "Committee to Elect House Republicans"


def build(first_name, district, counts, towns, with_phone, total, filename, asof):
    in_hand, not_mailed, voted = counts

    def bucket_row(label, n, note, last=False):
        border = '' if last else f'border-bottom:1px solid {RULE};'
        return f"""<tr>
            <td style="padding:10px 0;{border}font:15px/1.4 Arial,Helvetica,sans-serif;color:{INK};">
              <strong>{label}</strong><br><span style="color:{MUTED};font-size:14px;">{note}</span></td>
            <td style="padding:10px 0;{border}font:700 19px/1.2 Arial,Helvetica,sans-serif;color:{BRAND};text-align:right;white-space:nowrap;vertical-align:top;">{n}</td>
          </tr>"""

    rows = (bucket_row("Ballot in hand", in_hand, "Mailed to them, not yet returned.")
            + bucket_row("Ballot not mailed yet", not_mailed, "Requested; the clerk has not sent it out.")
            + bucket_row("Already voted", voted, "Completed and back with the clerk.", last=True))

    notes = [
        ("Ballot in hand",
         "The clerk has mailed it. They can complete and return it at any point from now on."),
        ("Ballot not mailed yet",
         "They have asked for one but the clerk has not sent it out."),
        ("Already voted",
         "Their completed ballot is back with the clerk."),
        ("Phone",
         f"Present for {with_phone} of the {total}, from the state file and our own records."),
        ("Lean",
         "How the voter is registered, and for undeclared voters which primary ballots they have taken in past state primaries."),
    ]
    step_html = "".join(
        f"""<tr>
              <td style="padding:0 14px 12px 0;vertical-align:top;font:700 15px/1.5 Arial,Helvetica,sans-serif;color:{INK};white-space:nowrap;">{t}</td>
              <td style="padding:0 0 12px 0;vertical-align:top;font:15px/1.5 Arial,Helvetica,sans-serif;color:{MUTED};">{d}</td>
            </tr>""" for t, d in notes)

    townline = ", ".join(towns) if towns else district

    html = f"""<body style="margin:0;padding:0;background:#f2f2f2;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f2f2f2;">
 <tr><td align="center" style="padding:24px 12px;">
  <table role="presentation" width="600" cellpadding="0" cellspacing="0" style="width:600px;max-width:100%;background:#ffffff;border-top:4px solid {BRAND};">

   <tr><td style="padding:26px 32px 0 32px;">
     <img src="{LOGO}" alt="{ORG}" width="150" style="display:block;border:0;max-width:150px;height:auto;">
   </td></tr>

   <tr><td style="padding:22px 32px 0 32px;">
     <h1 style="margin:0;font:700 23px/1.25 Arial,Helvetica,sans-serif;color:{INK};">Who already has a primary ballot in {district}</h1>
     <p style="margin:10px 0 0 0;font:16px/1.55 Arial,Helvetica,sans-serif;color:{MUTED};">
       {first_name}, attached is every voter in {district} who has asked the clerk for a <strong>Republican</strong> primary ballot. {total} of them, {with_phone} with a phone number.</p>
   </td></tr>

   <tr><td style="padding:24px 32px 0 32px;">
     <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-left:3px solid {BRAND};">
      <tr><td style="padding:2px 0 2px 14px;">
        <p style="margin:0 0 4px 0;font:700 12px/1.2 Arial,Helvetica,sans-serif;color:{BRAND};letter-spacing:.09em;text-transform:uppercase;">Why we are sending it</p>
        <p style="margin:0;font:15px/1.7 Arial,Helvetica,sans-serif;color:{INK};">
          These voters are casting a ballot in <strong>your primary</strong>. Once a ballot is completed and returned it cannot be changed, so the window on each name closes at a different time.</p>
      </td></tr>
     </table>
   </td></tr>

   <tr><td style="padding:26px 32px 0 32px;">
     <p style="margin:0 0 4px 0;font:700 12px/1.2 Arial,Helvetica,sans-serif;color:{BRAND};letter-spacing:.09em;text-transform:uppercase;">What is on the list</p>
     <p style="margin:0 0 10px 0;font:14px/1.5 Arial,Helvetica,sans-serif;color:{MUTED};">{townline}. Grouped by status, then alphabetically.</p>
     <table role="presentation" width="100%" cellpadding="0" cellspacing="0">{rows}</table>
   </td></tr>

   <tr><td style="padding:26px 32px 0 32px;">
     <p style="margin:0 0 12px 0;font:700 12px/1.2 Arial,Helvetica,sans-serif;color:{BRAND};letter-spacing:.09em;text-transform:uppercase;">What the columns mean</p>
     <table role="presentation" width="100%" cellpadding="0" cellspacing="0">{step_html}</table>
   </td></tr>

   <tr><td style="padding:8px 32px 0 32px;">
     <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
      <tr><td style="border-top:1px solid {RULE};padding-top:18px;">
        <p style="margin:0 0 8px 0;font:15px/1.6 Arial,Helvetica,sans-serif;color:{INK};"><strong>The file.</strong> <span style="color:{MUTED};">{filename}, opens in Excel or Numbers. Name, address, phone, what they registered as, and which bucket they are in.</span></p>
        <p style="margin:0 0 8px 0;font:15px/1.6 Arial,Helvetica,sans-serif;color:{INK};"><strong>Only Republican ballots.</strong> <span style="color:{MUTED};">Voters who asked for a Democratic ballot are not on here. They cannot vote in your primary.</span></p>
        <p style="margin:0;font:15px/1.6 Arial,Helvetica,sans-serif;color:{INK};"><strong>Updates are coming.</strong> <span style="color:{MUTED};">This is accurate to {asof}. The state refreshes the file regularly and we will send you an updated list each time it changes, so you can see who has moved from one column to the next.</span></p>
      </td></tr>
     </table>
   </td></tr>

   <tr><td style="padding:22px 32px 30px 32px;">
     <p style="margin:0;font:14px/1.6 Arial,Helvetica,sans-serif;color:{MUTED};border-top:1px solid {RULE};padding-top:16px;">
       Reply to this email if the list looks wrong for your district or you want it cut a different way.</p>
     <p style="margin:14px 0 0 0;font:13px/1.5 Arial,Helvetica,sans-serif;color:#8a8a8a;">{ORG}</p>
   </td></tr>

  </table>
 </td></tr>
</table>
</body>"""

    text = f"""{first_name},

Attached is every voter in {district} who has asked the clerk for a Republican
primary ballot. {total} of them, {with_phone} with a phone number.

WHY WE ARE SENDING IT
These voters are casting a ballot in your primary. Once a ballot is completed
and returned it cannot be changed, so the window on each name closes at a
different time.

WHAT IS ON THE LIST
{townline}. Grouped by status, then alphabetically.

  Ballot in hand          {in_hand}   Mailed to them, not yet returned.
  Ballot not mailed yet   {not_mailed}   Requested; the clerk has not sent it out.
  Already voted           {voted}   Completed and back with the clerk.

WHAT THE COLUMNS MEAN
  Ballot in hand         The clerk has mailed it. They can complete and return
                         it at any point from now on.
  Ballot not mailed yet  They have asked for one but the clerk has not sent it.
  Already voted          Their completed ballot is back with the clerk.
  Phone                  Present for {with_phone} of the {total}, from the state file and
                         our own records.
  Lean                   How the voter is registered, and for undeclared voters
                         which primary ballots they have taken in past state
                         primaries.

THE FILE
{filename}, opens in Excel or Numbers. Name, address, phone, registration and
bucket. Only Republican ballots are included - Democratic-ballot voters cannot
vote in your primary.

UPDATES ARE COMING
This is accurate to {asof}. The state refreshes the file regularly and we will
send you an updated list each time it changes, so you can see who has moved from
one column to the next.

Reply to this email if the list looks wrong for your district or you want it cut
a different way.

{ORG}
"""
    return html, text
