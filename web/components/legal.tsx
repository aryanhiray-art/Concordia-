"use client";

// Shared legal documents. Rendered by /terms and /privacy for signed-in members,
// and directly by AuthGate for visitors (AppShell shows AuthGate on every route
// when logged out, so these components ARE the public pages).
import { useEffect } from "react";

// ─────────────────────────────────────────────────────────────────────────────
// There is deliberately NO operator legal name and NO governing-law clause in
// these documents: the club is not incorporated yet, so naming an entity or
// nominating a forum would assert something that does not exist. The passages
// below say that plainly instead, which is also a fact a member should have
// before committing money. Add both together, once there is an entity — not
// one without the other.
//
// SEND-ONLY TODAY: Resend delivers the club's outbound mail and does not
// receive. This address needs an MX record or a forward before the promises
// attached to it below ("formal notice", "expect a reply") are ones it can
// keep. That is a DNS change, not a code change.
const OPERATOR_CONTACT = "legal@voteconcordia.com";
// ─────────────────────────────────────────────────────────────────────────────

function Doc({ eyebrow, title, footer, children }: {
  eyebrow: string; title: string; footer: string; children: React.ReactNode;
}) {
  // client-rendered content: jump to #risks etc. once the anchors exist (deferred past layout)
  useEffect(() => {
    const id = window.location.hash.slice(1);
    if (!id) return;
    const t = setTimeout(() => document.getElementById(id)?.scrollIntoView(), 0);
    return () => clearTimeout(t);
  }, []);
  return (
    <article className="glass shine enter p-6 sm:p-9">
      <div className="label">{eyebrow}</div>
      <h1 className="mt-2.5 font-display text-[24px] font-bold tracking-tight">{title}</h1>
      {children}
      <p className="mt-10 border-t border-white/10 pt-5 text-[12px] leading-relaxed text-subtle">{footer}</p>
    </article>
  );
}

function H({ id, children }: { id?: string; children: React.ReactNode }) {
  return (
    <h2 id={id} className="mt-8 scroll-mt-24 font-display text-[16.5px] font-bold tracking-tight">
      {children}
    </h2>
  );
}
function P({ children }: { children: React.ReactNode }) {
  return <p className="mt-3 text-[13.5px] leading-relaxed text-muted">{children}</p>;
}
function Li({ children }: { children: React.ReactNode }) {
  return (
    <li className="mt-2 pl-4 text-[13.5px] leading-relaxed text-muted marker:text-teal">{children}</li>
  );
}
function A({ href, children }: { href: string; children: React.ReactNode }) {
  return <a href={href} className="font-semibold text-teal hover:underline">{children}</a>;
}

// v3 (2026-08-28): the intro, §3 and §4 carry the live WEEKLY cadence (ballot
// closes Mon 09:15 ET, trades 10:15, Tue top-up, power every other Sunday) in
// place of the club's old daily one, §1 describes the operator as one
// individual, and §15 no longer nominates a governing law. PrivacyDoc went to
// v2 the same day (login-IP disclosure + the three processors).
//
// These footers and auth.py's CONSENT_KIND/PRIVACY_KIND move together — a
// version string that does not match the text it stamps makes every stored
// acceptance ambiguous. Members who accepted v2 remain recorded against v2;
// nothing re-prompts them, by decision.
export function TermsDoc() {
  return (
    <Doc
      eyebrow="Concordia · club agreement"
      title="Club Participation Agreement"
      footer="Version club_agreement_v3 — August 2026. This is the club's member agreement; it is not legal advice."
    >
      <P>
        Concordia is a private, invite-only trading club. Members vote each week on what the club
        should hold, and the consensus is executed automatically in each member&apos;s own brokerage
        account. This agreement says, in plain English, what joining means and what you&apos;re
        accepting. Read it — especially the <A href="#risks">risks</A> — before you participate.
      </P>

      <H id="who">Who you are agreeing with</H>
      <P>
        Concordia is not a company. It is run by one individual — referred to here as the club
        operator, or &ldquo;we&rdquo; — and there is no incorporated entity standing behind it, no
        investor, and no staff. Reach us at{" "}
        <A href={`mailto:${OPERATOR_CONTACT}`}>{OPERATOR_CONTACT}</A>, which is both where questions
        about this agreement are answered and where formal notice under it should be sent. There is
        no support desk behind that address and no second operator: one person runs the club, which
        is a fact worth knowing before you rely on it (see <A href="#risks">the risks</A>).
      </P>

      <H>1 · Membership is voluntary</H>
      <P>
        Joining is your choice, participation is your choice, and leaving is your choice. There is no
        minimum commitment, no lock-up, and no penalty for stopping. You can pause or quit at any
        time (see <A href="#stopping">§7</A>).
      </P>

      <H>2 · Your account, your custody</H>
      <P>
        Every member trades in their <b className="text-ink">own</b> brokerage account (currently
        Robinhood). The club never pools money, never takes custody of your funds or securities, and
        cannot move money into or out of your account. You connect through the broker&apos;s own
        secure login — the club never sees your brokerage password — and you can revoke that
        connection whenever you like.
      </P>

      <H>3 · How the club trades</H>
      <P>
        The club runs <b className="text-ink">one ballot per cycle</b>, and the cycle is currently{" "}
        <b className="text-ink">weekly</b>. A ballot opens once the previous cycle&apos;s trade is
        done and stays open through the week; it closes{" "}
        <b className="text-ink">Monday at 09:15 ET</b>, and the club trades that same morning at{" "}
        <b className="text-ink">10:15 ET</b>. Members allocate their voting power across the stocks
        they want the club to hold. When the ballot closes, the votes are tallied into a consensus
        basket, and that basket is executed automatically, equal-weight, in each participating
        member&apos;s account — sized to the slice of your account you chose to commit (your
        allocation setting). Your voting power itself is recalculated on its own schedule (currently
        every other Sunday). Nobody hand-trades for the club; execution follows the votes. That also
        means the basket can include names you voted against — that is the nature of a consensus
        club.
      </P>
      <P>
        The cadence above is the club&apos;s current schedule, not a promise that it can never
        change. Whenever a ballot is open, the app shows you when it closes, and members are given
        notice before a change to the trading schedule takes effect.
      </P>

      <H>4 · Timing, settlement and deferred orders</H>
      <P>
        Orders are placed after the ballot decides, during market hours. In one session the club
        sells the names that dropped out of the new basket and buys the names that entered it. US
        equities settle on a T+1 basis (one business day), so when the cash from that morning&apos;s
        sales has not settled yet — or the market is closed — orders may be{" "}
        <b className="text-ink">deferred</b> and placed on the next trading day&apos;s top-up pass
        (currently Tuesday), once the funds settle. Prices move between the vote and the fill: the
        price the club decided at is not necessarily the price your account gets.
      </P>

      <H id="fees">5 · No fees</H>
      <P>
        The club charges nothing today. No membership fee, no management fee, no performance fee,
        no commission, no markup. If that ever changes, it will happen only with advance written
        notice, an updated agreement you explicitly accept, and only once any registrations the law
        requires for a paid service are in place — you will never be silently charged under this
        version. Any costs your broker or regulators impose are between you and your broker.
      </P>

      <H>6 · Not investment advice</H>
      <P>
        Nothing in Concordia is investment advice or a recommendation. Ballots, votes, consensus
        baskets, research shown in the app, and anything members say are the aggregated opinions of
        club members — not advice tailored to you or anyone. The club and its operator are not your
        investment adviser and not your broker. You alone decide whether the club&apos;s approach
        fits your situation and how much, if anything, to commit.
      </P>

      <H id="risks">Risk disclosure</H>
      <P>Participating means accepting real financial risk. Concretely:</P>
      <ul className="mt-1 list-disc pl-5">
        <Li>
          <b className="text-ink">You can lose money — including the full amount you commit.</b>{" "}
          Stocks go down as well as up, and there is no protection against loss.
        </Li>
        <Li>
          <b className="text-ink">Past performance does not predict future results.</b> The
          club&apos;s history, charts, and any backtests tell you what happened, not what will
          happen.
        </Li>
        <Li>
          <b className="text-ink">Concentration.</b> The consensus basket can hold only a few names.
          A concentrated basket swings harder than a broad index.
        </Li>
        <Li>
          <b className="text-ink">Automation risk.</b> Execution is automated. Software bugs, broker
          outages, connectivity failures, or unexpected fills can cause orders to be missed, delayed,
          or filled at worse prices than expected.
        </Li>
        <Li>
          <b className="text-ink">Market risk.</b> Gaps, halts, and thin liquidity can move prices
          sharply between the club&apos;s decision and your fill.
        </Li>
        <Li>
          <b className="text-ink">Broker dependency.</b> The club can only trade through
          Robinhood&apos;s agentic API, which Robinhood controls. It can change those terms,
          restrict that access, or withdraw it — for the club or for you — at any time and without
          telling us first. If that happens the club cannot trade, and may not be able to exit
          positions on your behalf.
        </Li>
        <Li>
          <b className="text-ink">One operator.</b> One person builds and runs this. If they are
          unavailable, cycles can be missed and problems can go unfixed until they are back. There
          is no team, no on-call rotation, and no service-level promise.
        </Li>
        <Li>
          <b className="text-ink">Tax drag.</b> A weekly rebalance generates short-term gains and
          probably wash sales in your own account, and the bill can arrive in a year the club lost
          money. This one surprises people — see <A href="#tax">§11</A>.
        </Li>
        <Li>
          <b className="text-ink">Consensus risk.</b> Your vote may lose, and your account will
          still follow the winning basket while you participate.
        </Li>
      </ul>

      <H id="stopping">7 · Stopping — the kill switch</H>
      <P>
        Participation has a single switch. Turn <b className="text-ink">Participate</b> off in the
        app and the club stops placing new orders in your account immediately. Your existing
        positions stay exactly where they are — they are yours to keep or sell, on your own.
        Disconnecting Robinhood in the app revokes the club&apos;s access to your account entirely.
        To leave the club altogether, use <b className="text-ink">Delete account</b> in the app: all
        trading for you stops instantly, the club&apos;s Robinhood access and every credential are
        destroyed, and your personal details are erased. Your positions are not touched — they were
        always in your own account. The club&apos;s anonymized books (votes and trade records with
        your identity removed) and the record of your consent to this agreement are retained; see
        the <A href="/privacy">privacy note</A>.
      </P>

      <H id="robinhood-access">8 · Exactly what the club can do in your Robinhood</H>
      <P>
        Connecting uses Robinhood&apos;s own Agentic Trading authorization, scoped to a dedicated
        &ldquo;agentic&rdquo; brokerage account. Concordia <b className="text-ink">can</b>: read
        that account&apos;s balance, positions and order status; fetch market quotes; and submit
        market orders for stocks/ETFs <b className="text-ink">in that account only</b>, each tagged
        as agentic and subject to Robinhood&apos;s own checks. Concordia{" "}
        <b className="text-ink">cannot</b>: withdraw or transfer money or securities, see or change
        your Robinhood login, touch any other Robinhood account of yours (individual, retirement,
        spending, crypto), open options or crypto positions, or act outside the agentic account.
        Your credentials never pass through Concordia — authorization happens on Robinhood&apos;s
        site, and the resulting tokens are stored encrypted. You can revoke this access at any time
        from Concordia (Disconnect / Delete account) or from Robinhood itself.
      </P>

      <H>9 · Your data</H>
      <P>
        The club stores only what it needs to run: who you are, how you log in, encrypted broker
        tokens, your votes, and the trades placed for you. The full inventory — what, where, who can
        see it, and how to have it deleted — is in the <A href="/privacy">privacy note</A>.
      </P>

      <H>10 · Changes to this agreement</H>
      <P>
        This agreement may be updated. Members will be given notice before a new version takes
        effect, and the version you accepted — and when — is recorded with your membership.
      </P>

      <H id="tax">11 · Tax — the part people miss</H>
      <P>
        Rebalancing weekly has tax consequences that surprise most people, so they are spelled out
        rather than left to be discovered in February:
      </P>
      <ul className="mt-1 list-disc pl-5">
        <Li>
          <b className="text-ink">Almost every gain is short-term.</b> Positions are held for weeks,
          not the year-and-a-day that earns long-term capital-gains treatment. Short-term gains are
          taxed as ordinary income, at your regular rate.
        </Li>
        <Li>
          <b className="text-ink">Wash sales are likely.</b> A name that leaves the basket is sold,
          and it can re-enter and be re-bought within thirty days. Where it was sold at a loss, the
          IRS wash-sale rule may disallow that loss. The club does not track, avoid, or optimize for
          wash sales, and does not harvest losses.
        </Li>
        <Li>
          <b className="text-ink">Every cycle is a taxable event in your own account.</b> Your broker
          reports these trades to you and to the IRS on Form 1099-B. The liability is yours, and it
          can come due in a year the club lost money — realized gains and unrealized losses are
          different things.
        </Li>
        <Li>
          <b className="text-ink">Nobody here is your tax adviser.</b> The club does not give tax
          advice and does not consider your tax position when the basket changes. If any of the
          above matters to you, speak to your own tax professional before you participate.
        </Li>
      </ul>

      <H id="conflicts">12 · Conflicts of interest</H>
      <P>
        The club operator is also a member: they hold a stake, they vote, and the basket trades in
        their account alongside yours. They are paid nothing by the club and earn nothing from your
        participation (see <A href="#fees">§5</A>). Members are processed one after another inside a
        single automated run, so fills can differ slightly between accounts; that ordering is not
        chosen to favor anyone, and no member&apos;s orders are held back for another&apos;s.
      </P>

      <H id="no-promises">13 · What the club does not promise</H>
      <P>
        This is software run by one person on top of services nobody here controls. We do not promise
        that the app, the Robinhood connection, market data or the club&apos;s email will be
        available, timely or free of error; that a ballot will open, close or execute at the stated
        time; or that any order will be placed, filled, or filled at any particular price. A cycle
        can be missed outright. Neither side is liable to the other for indirect or consequential
        losses — lost profits, lost opportunity, the trade you would have made instead.
      </P>
      <P>
        What this agreement does <b className="text-ink">not</b> do is excuse our own conduct.{" "}
        <b className="text-ink">
          Nothing here limits our liability for gross negligence, recklessness, willful misconduct,
          bad faith or fraud, and nothing here waives or limits any right or remedy you have under
          federal or state securities laws.
        </b>{" "}
        Those rights were never ours to take. You will find a broad &ldquo;we are not liable for
        anything&rdquo; clause in most agreements that look like this one; its absence here is
        deliberate.
      </P>

      <H id="winding-down">14 · Suspension, removal, and winding down</H>
      <P>
        We can suspend or end your membership — if you break this agreement, if your broker
        connection is stuck in a state the club cannot trade from safely, or if we are required to.
        Where it is practical you get notice first; where it is not, you get it as soon as possible
        afterwards. You can always leave first (see <A href="#stopping">§7</A>).
      </P>
      <P>
        The club itself may stop, and if it does, stopping is the whole of it:{" "}
        <b className="text-ink">your positions stay in your own account, in your name, untouched</b>,
        because they were never anywhere else. Nothing has to be unwound, returned or distributed —
        there is no fund to close and no queue to be last in. Members will get as much notice as the
        circumstances allow, and the app will say plainly that the club has stopped trading.
      </P>

      <H id="disputes">15 · What happens if we disagree</H>
      <P>
        This agreement does not nominate a governing law or a court, because there is no company to
        attach one to. Whatever law would apply between two people in our respective places applies
        here, and neither of us is giving up a right we would otherwise have — you are not waiving a
        jury, and nothing here forces arbitration. If Concordia is ever incorporated, this section
        will change and you will be told before it does.
      </P>
      <P>
        Before that: write to <A href={`mailto:${OPERATOR_CONTACT}`}>{OPERATOR_CONTACT}</A> setting
        out the problem and what would settle it, and give us thirty days to put it right. Nearly
        everything ends there. Either of us can still take a qualifying claim to small-claims court.
      </P>
      <P>
        This agreement does <b className="text-ink">not</b> force you into arbitration and does not
        ask you to give up class actions. That is a choice, not an oversight: those clauses are under
        active regulatory scrutiny in agreements of exactly this kind, and a club this small gains
        nothing from them worth what they take from a member.
      </P>
    </Doc>
  );
}

export function PrivacyDoc() {
  return (
    <Doc
      eyebrow="Concordia · privacy"
      title="Privacy — what we store and why"
      footer="Version privacy_v2 — August 2026. This note describes the club's actual data handling. It is not legal advice."
    >
      <P>
        Concordia stores the minimum it needs to run a voting club that executes in your own
        brokerage account. No analytics trackers, no ad networks, no data brokers.
      </P>
      <P>
        The club is run by one individual, not a company, who is responsible for everything
        described here and answers questions about it at{" "}
        <A href={`mailto:${OPERATOR_CONTACT}`}>{OPERATOR_CONTACT}</A>.
      </P>

      <H>What we store</H>
      <ul className="mt-1 list-disc pl-5">
        <Li><b className="text-ink">Name and email</b> — to identify you to the club.</Li>
        <Li>
          <b className="text-ink">Login credentials</b> — a hashed password (never the password
          itself), or, if you sign in with Google, your Google account identifier and verified
          email.
        </Li>
        <Li>
          <b className="text-ink">Sign-in records</b> — the time and the IP address of each signup
          and login, kept as a basic security record. These are erased when you delete your account.
        </Li>
        <Li>
          <b className="text-ink">Encrypted brokerage tokens</b> — the OAuth tokens Robinhood issues
          when you connect, stored encrypted. The club never sees or stores your Robinhood password.
        </Li>
        <Li>
          <b className="text-ink">Account identifiers</b> — your brokerage account number and the
          balance/position snapshots needed to size orders and show your performance.
        </Li>
        <Li>
          <b className="text-ink">One cookie</b> — a sign-in cookie that keeps you logged in for
          thirty days. Scripts cannot read it, it goes nowhere, and it is the only cookie the club
          sets. There are no analytics or advertising cookies, because there is no analytics and no
          advertising.
        </Li>
        <Li><b className="text-ink">Votes</b> — every ballot you cast.</Li>
        <Li>
          <b className="text-ink">Trade records</b> — the orders the club placed in your account,
          their status and fills.
        </Li>
        <Li>
          <b className="text-ink">Consent records</b> — which version of the club agreement you
          accepted, and when.
        </Li>
      </ul>

      <H>Where it lives</H>
      <P>
        On the club&apos;s own server — a single machine run by the club operator, hosted in a US
        datacenter of an EU-owned hosting provider. That server is the club&apos;s records; there is
        no second copy scattered across other platforms.
      </P>

      <H>The outside services involved</H>
      <P>
        Running the club takes three, and nothing of yours goes anywhere else:
      </P>
      <ul className="mt-1 list-disc pl-5">
        <Li>
          <b className="text-ink">Robinhood</b> — your broker. It holds the account and receives the
          orders the club places in it. You authorize this yourself, and you can revoke it at any
          time (see <A href="/terms#robinhood-access">§8 of the agreement</A>).
        </Li>
        <Li>
          <b className="text-ink">Google</b> — only if you choose to sign in with Google, and only to
          confirm the account is yours. Sign in with a password and Google is not involved at all.
        </Li>
        <Li>
          <b className="text-ink">Resend</b> — the service that delivers the club&apos;s email. It
          receives your email address and the contents of each message sent to you, which for a
          post-trade receipt includes what the club bought and sold in your account that cycle.
        </Li>
      </ul>

      <H>Who can access it</H>
      <P>
        The club operator only, and only to run the club. Other members see your display name, your
        votes in club tallies, and club-level results — never your email, tokens, balances, or
        account numbers.
      </P>

      <H>How long it is kept</H>
      <P>
        Your personal details are kept while you are a member and erased the moment you delete your
        account — immediately, not on a monthly sweep. Sign-in records go at the same time. The
        club&apos;s books — votes, orders and voting-power history, with your identity removed — are
        kept <b className="text-ink">indefinitely</b>, because they are the permanent record of what
        the club decided and did; so is the fact that you accepted the agreement, and when. Nothing
        is kept &ldquo;just in case&rdquo; beyond that.
      </P>

      <H>Your rights over your data</H>
      <P>
        Ask and you get: a copy of everything the club holds about you in a readable format, a
        correction to anything wrong, and deletion — which you can also do yourself, any time, from
        the Account page. Write to{" "}
        <A href={`mailto:${OPERATOR_CONTACT}`}>{OPERATOR_CONTACT}</A> and expect a reply within
        thirty days. The club is far below the size at which the state privacy statutes apply by
        their own thresholds; these rights are offered because they are the right way to treat
        members, not because a law currently compels it.
      </P>

      <H>If something goes wrong</H>
      <P>
        If member data is exposed — or there is reason to believe it was — affected members are told
        what happened, what was involved and what to do about it, within{" "}
        <b className="text-ink">thirty days</b> of the club becoming aware of it. That is the
        standard the SEC sets for registered firms under Regulation S-P. The club is not a
        registered firm, and holds itself to it anyway.
      </P>

      <H>Age</H>
      <P>
        Membership is 18 and over. The club does not knowingly hold data on anyone younger; if that
        ever happens, tell us and it will be deleted.
      </P>

      <H>What we never do</H>
      <P>
        Your data is never sold, rented, or handed to anyone beyond the three services above — and
        each of those acts only on the club&apos;s instructions, for the one job named. There is
        nothing to opt out of, because there is no marketing use at all.
      </P>

      <H>Deletion</H>
      <P>
        Delete your account yourself, any time, from the Account page. Deletion is immediate:
        trading for you stops, the encrypted Robinhood tokens and account link are destroyed, your
        sign-in methods (password, Google) are removed, and your name and email are erased. What is
        retained: the club&apos;s anonymized books (votes, orders and voting-power history with your
        identity removed — kept because they are the shared record of what the club did) and the
        record that you accepted the agreement (kept as proof of consent). Trades already executed
        also live on in your own brokerage account&apos;s records, which the club doesn&apos;t
        control. See also the <A href="/terms">Club Participation Agreement</A>.
      </P>

      <H>Changes to this policy</H>
      <P>
        This privacy note can change — for example if the club adds features, providers, or legal
        obligations. When it does, members get notice before the new version applies, the version
        number above changes, and the version you accepted — and when — stays recorded with your
        membership. Continued participation after notice means acceptance; if you disagree with a
        change, you can stop participating or delete your account at any time.
      </P>
    </Doc>
  );
}
