# Privacy Policy

Effective {{effective_date}}

This policy explains what personal data thestill handles, why, who else sees it and what you can do about it. It covers visitors, signed-in users and people who are mentioned in podcasts on thestill.

In short: public pages set no cookies and load no third-party scripts, and we count visits on our own server without identifying you. We do not sell your data, show advertising, or use your data to train AI models.

## Who is responsible

thestill is run by {{operator_name}}, {{operator_description}}. We are the "controller" of your personal data under the UK GDPR and, where it applies, the EU GDPR. Contact us about privacy at {{privacy_email}}, or by post at {{operator_address}}.

## Signed-out visitors

Like any web server, ours keeps request logs (IP address, browser, page requested and time) for security and rate limiting. We keep them for up to {{log_retention}}.

To show the podcast chart for your country, we look up the country of your IP address with ipinfo.io. We keep the answer for a day and do not store your address with it.

We count page views to learn which pages people find useful: the kind of page (for example "an episode page"), the website that sent you, and a visitor code that changes every day so the same visit is not counted twice. The code is made from your IP address and browser in a way that cannot be reversed, and neither is stored. If your browser sends Global Privacy Control, we count the view without the code. See "Usage analytics" below.

Legal basis: our legitimate interest in running a secure service and showing relevant content.

## Signed-in users

| What | Why | Legal basis |
| --- | --- | --- |
| Name, email address, profile picture and Google account ID, from Google sign-in | To create and run your account | Contract |
| Your country, looked up from your IP address on first sign-in | To pick your default chart; you can change it in Settings | Legitimate interests |
| Shows you follow, your inbox and what you have read, briefings, settings, searches and episodes you import | To provide the features you use | Contract |
| Briefing email delivery and unsubscribe records | To send briefings by email, if you turn that on | Contract |
| Access tokens you create for connecting an AI assistant | To let that assistant read your thestill data on your behalf | Contract |
| Which features you use, such as pages by type, searches (not what you searched for), shares and AI assistant requests | To learn what to improve; you can turn it off in Settings | Legitimate interests |
| Messages you send us | To answer you and handle requests | Legitimate interests |

"Contract" means we need the data to provide the service you signed up for. "Legitimate interests" means we have weighed our interest against your privacy and your rights; you can object (see "Your rights").

We do not make decisions about you based solely on automated processing that have legal or similarly significant effects.

## People mentioned in podcasts

thestill transcribes public podcast episodes, so it processes what people say on them. For people who are named or quoted, we may show a person page with their name, a short description and links from Wikidata, the episodes they appear in and short quotes, each linked to its source.

The data comes from public podcast feeds and Wikidata. We use it to make podcasts searchable with accurate attribution, which is our legitimate interest and that of our readers. We do not combine it with other sources to profile people or sell it.

If you are named on thestill, you can ask us to hide your person page, correct a quote or remove your data from thestill's pages, at {{privacy_email}}. We do not need a reason.

## Usage analytics

We use PostHog, hosted in the EU, to see how thestill is used. It receives:

- for signed-in users: your internal account number (not your name or email), whether you are an administrator, the kind of page you open, and events such as "searched", "shared an episode" or "an AI assistant used a tool", with counts but never the text you searched for, page text or full web addresses;
- for signed-out visitors: the page views described above, without any account number;
- the browser, operating system and device type, worked out from your browser's user agent.

PostHog does not receive your IP address: your browser's events pass through our own server, which removes it before forwarding them, and the events our server sends come from the server's own address. Signed-out visitors' browsers never contact PostHog at all: our own server forwards the page views.

You can turn usage analytics off at any time in Settings, and we honour Global Privacy Control. Either way, the things the service has to keep to work (such as the shows you follow and which inbox episodes you have read) still count towards our overall numbers. What we record only to measure, such as when you first opened an episode or last played one, is not recorded while usage analytics is off, and turning it off deletes what we had recorded.

## Cookies and browser storage

Public pages set no cookies. When you sign in, we set:

- a session cookie that keeps you signed in, for up to 30 days;
- two short-lived cookies during sign-in that protect it against forgery and remember where to send you back, for 10 minutes.

The app also keeps small preferences, such as scroll position and filters, in your browser's storage. All of this is strictly necessary to provide the service, so we do not ask for consent. We use no advertising or analytics cookies: usage analytics keeps nothing in your browser.

## Who we share data with

We use these service providers ("processors") to run thestill. They may only use the data to provide their service to us.

| Provider | What they do | Data they receive |
| --- | --- | --- |
| Amazon Web Services, London (UK) | Hosting, database, backups, server logs and sending briefing emails | Everything thestill stores |
| Google Gemini API (US and other countries) | Writing summaries, briefings and person descriptions | Podcast transcripts and episode information; for briefings, the episodes in your briefing, not your name or email |
| ipinfo.io (US) | Looking up the country of an IP address | Your IP address |
| PostHog, EU hosting (Germany) | Usage analytics (see "Usage analytics") | Internal account number, which features you use, pages by type, browser and device type; not your IP address |

Google also runs "Sign in with Google". For that, Google acts as its own controller under its own privacy policy, and learns that you signed in to thestill.

If you connect an AI assistant (for example Claude or ChatGPT) to thestill, the data it reads is handled under your agreement with that assistant's provider, not by us.

We may also disclose data if the law requires it, to protect people's safety or our legal rights, or to a new operator if thestill is transferred (see "Changes"). We will never sell it.

## International transfers

Some providers process data outside the UK and the European Economic Area. Where they do, we rely on safeguards the law recognises, such as the UK–US data bridge, the EU–US Data Privacy Framework or standard contractual clauses.

## How long we keep data

| Data | Kept |
| --- | --- |
| Account and activity data | Until you delete your account |
| Server request logs | Up to {{log_retention}} |
| Country lookups for signed-out visitors | One day |
| Usage analytics events | Up to {{analytics_retention}} |
| Backups | Up to 60 days, then overwritten |

When you delete your account, we delete your account data within 30 days. It may remain in backups for up to 60 more days until they expire.

## Your rights

You have the right to:

- get a copy of your data;
- correct data that is wrong;
- have your data deleted;
- restrict or object to how we use it;
- receive your data in a machine-readable format;
- withdraw any consent you have given.

To use any of these rights, including deleting your account, email {{privacy_email}} from the address on your account. We will reply within one month.

If you are unhappy with how we handle your data, please tell us first. You can also complain to {{supervisory_authority}}, or to the data protection authority where you live.

## Security

Connections to thestill use HTTPS. Session cookies cannot be read by scripts on the page. Servers are reachable only through access-controlled channels, and backups are encrypted.

## Children

thestill is not meant for children under 16, and we do not knowingly collect their data. If you believe a child has signed up, contact us and we will delete the account.

## Changes

We may update this policy as thestill changes or the law requires. We will change the date at the top and, for significant changes, tell signed-in users by email or in the app before they take effect.

If thestill is transferred to another operator, such as a company set up to run it, that operator will become responsible for your data under this policy, and we will tell you.
