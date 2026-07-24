"use client";

// Shared legal documents. Rendered by /terms and /privacy for signed-in members,
// and directly by AuthGate for visitors (AppShell shows AuthGate on every route
// when logged out, so these components ARE the public pages).
import { useEffect } from "react";

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

export function TermsDoc() {
  return (
    <Doc
      eyebrow="Concordia · club agreement"
      title="Club Participation Agreement"
      footer="Version club_agreement_v2 — July 2026. This is the club's member agreement; it is not legal advice."
    >
      <P>
        Concordia is a private, invite-only trading club. Members vote once a day on what the club
        should hold, and the consensus is executed automatically in each member&apos;s own brokerage
        account. This agreement says, in plain English, what joining means and what you&apos;re
        accepting. Read it — especially the <A href="#risks">risks</A> — before you participate.
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
        Each trading day the club opens one ballot. Members allocate their voting power across the
        stocks they want the club to hold. When the ballot closes, the votes are tallied into a
        consensus basket, and that basket is executed automatically, equal-weight, in each
        participating member&apos;s account — sized to the slice of your account you chose to commit
        (your allocation setting). Nobody hand-trades for the club; execution follows the votes. That
        also means the basket can include names you voted against — that is the nature of a
        consensus club.
      </P>

      <H>4 · Timing, settlement and deferred orders</H>
      <P>
        Orders are placed after the ballot decides, during market hours when possible. US equities
        settle on a T+1 basis (one business day). When cash from a sale hasn&apos;t settled yet, or
        the market is closed, orders may be <b className="text-ink">deferred</b> and placed later,
        once funds settle or the market reopens. Prices move between the vote and the fill: the price
        the club decided at is not necessarily the price your account gets.
      </P>

      <H>5 · No fees</H>
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
    </Doc>
  );
}

export function PrivacyDoc() {
  return (
    <Doc
      eyebrow="Concordia · privacy"
      title="Privacy — what we store and why"
      footer="Version privacy_v1 — July 2026. This note describes the club's actual data handling. It is not legal advice."
    >
      <P>
        Concordia stores the minimum it needs to run a voting club that executes in your own
        brokerage account. No analytics trackers, no ad networks, no data brokers.
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
          <b className="text-ink">Encrypted brokerage tokens</b> — the OAuth tokens Robinhood issues
          when you connect, stored encrypted. The club never sees or stores your Robinhood password.
        </Li>
        <Li>
          <b className="text-ink">Account identifiers</b> — your brokerage account number and the
          balance/position snapshots needed to size orders and show your performance.
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
        datacenter of an EU-owned hosting provider. Data is not spread across third-party services.
      </P>

      <H>Who can access it</H>
      <P>
        The club operator only, and only to run the club. Other members see your display name, your
        votes in club tallies, and club-level results — never your email, tokens, balances, or
        account numbers.
      </P>

      <H>What we never do</H>
      <P>
        Your data is never sold, rented, or shared. There is nothing to opt out of, because there is
        no marketing use at all.
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
