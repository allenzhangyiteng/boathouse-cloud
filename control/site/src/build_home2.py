#!/usr/bin/env python3
"""The next home page, generated from the live template with content changes only. Output: src/home2.template.html,
which build.py turns into site/home2.html, served at /preview/home until it is approved."""
import pathlib
here = pathlib.Path(__file__).parent
s = (here / "index.template.html").read_text()
def rep(old, new, count=1):
    global s
    assert old in s, old[:90]
    s = s.replace(old, new, count)
rep('<title>Boat House · The Google Doc for small software</title>', '<title>Boat House · Preview</title>')
rep('<meta name="description"', '<meta name="robots" content="noindex,nofollow">\n<meta name="description"')
rep('<a href="#own">Yours</a>', '<a href="#why">Why</a>')
rep('<a href="#versus">$140 or $10</a>', '<a href="#versus">$65 or $10</a>')
rep('<p class="lead">Five minutes to set up. After that your agent does the rest: no dashboards, no second key to paste. Share it the way you share a Google Doc.</p>',
    '<p class="lead">The AI-native cloud to share and own the software you build. Your Claude Code, Codex or Cursor writes the tool; Boat House puts it online, logs in everyone you name, and keeps it yours.</p>')
rep('<p class="proof">Replaces six subscriptions that add up to <em>$140.92</em> a month. Boat House is <em>$10</em>, everything included.</p>',
    '<p class="proof"><em>$10</em> a month for up to five lightweight tools. <em>$0</em> a person. About 90 seconds to connect your agent after email confirmation. Funding and app builds take extra time.</p>')
rep('''    <h2>Boat House is where your agent puts small software online.</h2>
    <p class="lead center">One account. One line pasted into your agent. Shared like a doc, with whoever you say. Yours to keep: the code, the data, the address.</p>''',
    '''    <h2 id="why" class="whyh">Why people choose Boat House.</h2>
    <div class="glassgrid">
      <div class="glass g1"><div class="gk">01</div><h3>Easy</h3><ul>
        <li>Connect your agent in about 90 seconds</li>
        <li>Then plain English: “put it online”, “share it with Sam”</li>
        <li>No dashboards, no keys, no tickets</li>
        <li>If you can run a business, you can run this</li></ul></div>
      <div class="glass g2"><div class="gk">02</div><h3>Cheap</h3><ul>
        <li>Five lightweight tools for $10 a month</li>
        <li>Everyone who uses it: $0</li>
        <li>The usual stack: four accounts, about $65</li>
        <li>Team platforms: $500 and up for 25 people</li></ul></div>
      <div class="glass g3"><div class="gk">03</div><h3>Yours</h3><ul>
        <li>Your code, your data, your address</li>
        <li>Not Claude’s, not OpenAI’s, not ours</li>
        <li>Switch Claude Code → Codex → Cursor any time</li>
        <li>Leave with everything, in one file</li></ul></div>
      <div class="glass g4"><div class="gk">04</div><h3>Together</h3><ul>
        <li>Share it like a Google Doc: viewer, editor, admin</li>
        <li>By email, inside or outside your company</li>
        <li>Two people edit the software from their own agents</li>
        <li>Private by default, everyone signs in as themselves</li></ul></div>
    </div>''')
own_a = s.index('<section class="sec" id="own">'); own_b = s.index('</section>', own_a) + len('</section>\n\n')
s = s[:own_a] + s[own_b:]
rep('I need you to set up six services yourself and send me a key from each one:', 'I need you to set up four services and a domain yourself, and send me a key from each one:')
li_a = s.index('<li>{{MARK_sentry}}<span>'); li_b = s.index('</li>', li_a) + len('</li>\n')
s = s[:li_a] + s[li_b:].lstrip(' ')
rep('About an hour of your time across six dashboards. Six accounts, six cards on file. I cannot do any of it for you; each one needs you signed in.',
    'About an hour of your time across five dashboards. Four accounts, keys pasted into a file, a domain to point. I cannot do any of it for you; each one needs you signed in.')
rep('an hour of clicking ahead · 6 accounts · 6 cards on file · $140.92 a month', 'an hour of clicking ahead · 4 accounts · keys in a file · about $65 a month')
rep('<h2>Six subscriptions, or one. <span class="muted">The same tracker, two people who can change it, forty people who use it. Priced both ways at what they charge this month.</span></h2>',
    '<h2>Four accounts, or one. <span class="muted">The same tracker, two people who can change it, forty people who use it. Priced both ways at what they charge this month, free plans where a free plan honestly does the job.</span></h2>')
rep('<div class="vrow"><div class="mark">{{MARK_clerk}}</div><div class="name"><span>Clerk Pro</span><small>logins for the team</small></div><div class="amt">$25.00</div></div>',
    '<div class="vrow"><div class="mark">{{MARK_clerk}}</div><div class="name"><span>Clerk</span><small>logins for the team, on the free plan (Pro is $25)</small></div><div class="amt">$0.00</div></div>')
row_a = s.index('<div class="vrow"><div class="mark">{{MARK_sentry}}</div>'); row_b = s.index('</div></div>\n', row_a) + len('</div></div>\n')
s = s[:row_a] + s[row_b:].lstrip(' ')
rep('<div class="vrow"><div class="mark">{{MARK_resend}}</div><div class="name"><span>Resend Pro</span><small>sends the sign-in emails</small></div><div class="amt">$20.00</div></div>',
    '<div class="vrow"><div class="mark">{{MARK_resend}}</div><div class="name"><span>Resend</span><small>sends the sign-in emails, on the free plan (Pro is $20)</small></div><div class="amt">$0.00</div></div>')
rep('<div class="vrow sum"><div class="mark"></div><div class="name"><span>Every month</span><small>six accounts, six cards on file, seventeen tabs</small></div><div class="amt">$140.92</div></div>',
    '<div class="vrow sum"><div class="mark"></div><div class="name"><span>Every month</span><small>four accounts, two cards on file, keys pasted into a file</small></div><div class="amt">$66.92</div></div>')
rep('<h3 style="font-size:22px">Fourteen months of Boat House for one month the usual way.</h3>', '<h3 style="font-size:22px">Six months of Boat House for one month the usual way.</h3>')
rep('<p class="text" style="max-width:480px">Even the cheapest version of the usual way, one seat and every free plan, comes to $21.92 a month. And it has no nightly backups, and only one person who can put changes online.</p>',
    '<p class="text" style="max-width:480px">Team platforms charge per person instead. For twenty-five people: Softr $99 to $329, Retool $130 to $180, Airtable $500, Claude Team $750 a month. Boat House is $10, because people are free.</p>')
rep('<h2 class="big">$140.92 a month, or $10.</h2>', '<h2 class="big">Five lightweight tools for $10 a month. $0 a person.</h2>')
rep('<div class="stat rv"><b data-count="140.92" data-prefix="$">$140.92</b><p>what six subscriptions charge for one tracker, two people who can change it, forty who use it</p></div>',
    '<div class="stat rv"><b data-count="65" data-prefix="$">$65</b><p>what four accounts cost the usual way, for one tracker, two people who can change it, forty who use it</p></div>')
rep('<div class="stat dim rv"><b data-count="14" data-suffix="×">14×</b><p>months of Boat House for the price of one month the usual way</p></div>',
    '<div class="stat dim rv"><b data-count="6" data-suffix="×">6×</b><p>cheaper than the usual way, before counting the hour of setup it saves</p></div>')
rep('<p class="small band-note">Prices are what each company charges month to month, read from their pricing pages in September 2026. Paying yearly makes a few of them cheaper. Managing forty people as one team in the login service adds $100 a month; we left that out. Your own .com on Boat House is about a dollar a month more.</p>',
    '<p class="small band-note">Prices are what each company charges month to month, read from their pricing pages in September 2026. Team platforms charge per person, $99 to $750 a month for twenty-five people; Boat House charges per organization, and people are free. Your own .com is about a dollar a month more.</p>')
rep('Five minutes of setup turns a $140 bill into a $10 bill.', 'Connect your agent in about 90 seconds, then let it handle the move.')
rep('<small>one account, one balance, the first $10 on us</small>', '<small>one account, one balance, people free</small>') if 'the first $10 on us' in s else None
rep('<small>one account, one balance, no card to start</small>', '<small>one account, one balance, people free</small>')
rep('<h3>Sign up. No card.</h3><p class="text">An email address and a name for your workspace. Put money on the balance when you are ready to put something online; a friend\'s referral code makes every tool half price for 60 days.</p>',
    '<h3>Sign up.</h3><p class="text">An email address and a name for your workspace. Then $20 on the balance, two months of one tool, so the first deploy can run. A friend\'s referral code makes every tool half price for 60 days.</p>')
rep('Workspace made · no card asked for', 'Workspace made · $20 on the balance')
for q in ('What kind of things can I build here?', 'How do I put money on the balance?', 'Do I have to leave the agent I already use?'):
    a = s.index('<details><summary>' + q); b = s.index('</details>', a) + len('</details>\n')
    s = s[:a] + s[b:].lstrip(' ')
rep('</head>', '''<style>
.whyh{text-align:center;max-width:none;color:#fff;font-size:34px;margin-bottom:36px}
.glassgrid{display:grid;grid-template-columns:repeat(4,1fr);gap:18px;perspective:1200px}
.glass{position:relative;border-radius:14px;padding:24px 22px 26px;overflow:hidden;
  background:linear-gradient(160deg,rgba(255,255,255,.12) 0%,rgba(255,255,255,.04) 60%,rgba(140,181,251,.06) 100%);
  border:1px solid rgba(255,255,255,.16);box-shadow:inset 0 1px 0 rgba(255,255,255,.28),inset 0 -1px 0 rgba(0,0,0,.25),0 30px 60px rgba(0,0,0,.35);
  -webkit-backdrop-filter:blur(18px) saturate(1.4);backdrop-filter:blur(18px) saturate(1.4);
  transition:transform .4s cubic-bezier(.2,.8,.2,1),border-color .4s,box-shadow .4s;animation:floaty 8s ease-in-out infinite}
.glass.g2{animation-delay:-2s}.glass.g3{animation-delay:-4s}.glass.g4{animation-delay:-6s}
@keyframes floaty{0%,100%{transform:translateY(0)}50%{transform:translateY(-6px)}}
.glass:before{content:"";position:absolute;inset:0;background:radial-gradient(120% 80% at 12% 0%,rgba(140,181,251,.28),transparent 58%);pointer-events:none}
.glass:after{content:"";position:absolute;top:-70%;left:-45%;width:55%;height:240%;background:linear-gradient(100deg,transparent 30%,rgba(255,255,255,.16) 50%,transparent 70%);transform:rotate(14deg);animation:glide 9s ease-in-out infinite;pointer-events:none}
.glass.g2:after{animation-delay:1.3s}.glass.g3:after{animation-delay:2.6s}.glass.g4:after{animation-delay:3.9s}
@keyframes glide{0%,55%{left:-45%}100%{left:135%}}
.glass:hover{transform:translateY(-8px) rotateX(2deg);border-color:rgba(140,181,251,.6);box-shadow:inset 0 1px 0 rgba(255,255,255,.35),0 40px 80px rgba(0,0,0,.45),0 0 40px rgba(48,123,249,.25)}
.gk{font-family:var(--mono);font-size:11px;letter-spacing:.14em;color:var(--dark-muted);margin-bottom:14px}
.glass h3{color:#fff;font-size:26px;letter-spacing:-.02em;margin-bottom:12px}
.glass ul{list-style:none;padding:0;margin:0;display:grid;gap:9px}
.glass li{position:relative;padding-left:18px;color:#DCE6F7;font-size:14.5px;line-height:1.45}
.glass li:before{content:"";position:absolute;left:0;top:.55em;width:7px;height:7px;border-radius:50%;background:var(--accent);box-shadow:0 0 12px rgba(48,123,249,.9)}
@media(prefers-reduced-motion:reduce){.glass,.glass:after{animation:none}}
@media(max-width:1040px){.glassgrid{grid-template-columns:1fr 1fr}.whyh{font-size:28px}}
@media(max-width:640px){.glassgrid{grid-template-columns:1fr}}
</style>
</head>''')
assert '140.92' not in s and '$140' not in s, "old number left"
(here / "home2.template.html").write_text(s)
print("home2.template.html written from the live template")
