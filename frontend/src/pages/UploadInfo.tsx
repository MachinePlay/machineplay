import { Link } from 'react-router'

const STARTER_REPO = 'https://github.com/MachinePlay/python-chess-starter'

function Code({ children }: { children: string }) {
  return (
    <code className="block bg-neutral-900 border border-neutral-800 rounded px-3 py-2 text-sm font-mono text-neutral-200">
      {children}
    </code>
  )
}

export default function UploadInfo() {
  return (
    <div className="max-w-2xl mx-auto px-4 py-8 flex flex-col gap-5">
      <h1 className="text-xl font-semibold text-neutral-100">upload an engine</h1>
      <p className="text-neutral-400 text-sm">
        Engines are uploaded from the command line. Fork the starter template,
        write your engine, then build &amp; push it with the{' '}
        <span className="font-mono">machineplay</span> CLI.
      </p>

      <ol className="flex flex-col gap-3 text-sm text-neutral-300">
        <li className="flex flex-col gap-1">
          <span>1. Fork the starter template:</span>
          <a
            href={`${STARTER_REPO}/fork`}
            target="_blank"
            rel="noreferrer"
            className="text-neutral-100 underline hover:text-white break-all"
          >
            {STARTER_REPO}
          </a>
        </li>
        <li className="flex flex-col gap-1">
          <span>2. Install the CLI:</span>
          <Code>pip install machineplay</Code>
        </li>
        <li className="flex flex-col gap-1">
          <span>3. Log in (opens this site for a token):</span>
          <Code>machineplay login</Code>
        </li>
        <li className="flex flex-col gap-1">
          <span>4. From your engine folder, build &amp; upload:</span>
          <Code>machineplay upload</Code>
        </li>
      </ol>

      <p className="text-neutral-500 text-xs">
        Need a token now?{' '}
        <Link to="/cli" className="underline hover:text-neutral-300">
          Generate one here.
        </Link>
      </p>
    </div>
  )
}
