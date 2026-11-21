import { Link } from 'react-router'

import { Logo, Screen } from '../components/ui'

const SECTIONS: { title: string; body: string[] }[] = [
  {
    title: 'What we collect',
    body: [
      'Your name and mobile number, which you give us to sign in.',
      'Messages, terms, payment cards and bank or UPI details that you or your team enter in rooms.',
      'Your choices on AI suggestions (used, edited or ignored), so suggestions improve for you.',
      'Basic technical data needed to keep your session secure, such as your browser type.',
    ],
  },
  {
    title: 'Why we use it',
    body: [
      'To run Deal Rooms and Pay Lanes: chat, term sheets, approvals and payment status.',
      'To protect money movement: verified identities, risk checks and an audit trail.',
      'To show AI suggestions. Room messages are sent to our AI provider (OpenAI) only to generate them.',
    ],
  },
  {
    title: 'Who can see it',
    body: [
      'People in a room see that room only. Guests never see a team’s other rooms.',
      'The AI copilot, private floor price and notes are visible only to their owner on the freelancer side.',
      'Bank and UPI details are encrypted at rest and shown masked to the last 4 characters.',
      'Chat is delivered through Nexconn, our messaging provider. We do not sell your data.',
    ],
  },
  {
    title: 'Your rights',
    body: [
      'Under India’s Digital Personal Data Protection Act, 2023 you can ask to see, correct or erase your personal data, and withdraw consent at any time.',
      'Withdrawing consent stops new processing; records needed for an audit trail of completed payments are kept as the law requires.',
      'To make a request or raise a grievance, open an issue at github.com/Chiru595/pakkaa.',
    ],
  },
  {
    title: 'This build',
    body: [
      'This is a hackathon build. Payments are simulated and no SMS is sent in test mode.',
      'Rule packs show their source and date. Nothing in Pakkaa is legal or financial advice.',
    ],
  },
]

export default function Privacy() {
  return (
    <Screen>
      <div className="space-y-6 px-6 py-8">
        <Link to="/">
          <Logo />
        </Link>
        <div>
          <h1 className="text-2xl font-bold text-ink">Privacy notice</h1>
          <p className="text-sm text-slate-500">Version 2026-11-21</p>
        </div>
        {SECTIONS.map((s) => (
          <section key={s.title} className="space-y-2">
            <h2 className="font-semibold text-ink">{s.title}</h2>
            <ul className="list-disc space-y-1.5 pl-5 text-sm text-slate-700">
              {s.body.map((b) => (
                <li key={b}>{b}</li>
              ))}
            </ul>
          </section>
        ))}
      </div>
    </Screen>
  )
}
