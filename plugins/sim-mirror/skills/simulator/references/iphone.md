# Physical iPhone or iPad

Choose the requested phone from `sim_device list`. If `real_devices.agents_choose` refuses, the person selects it in
the viewer. Before gestures, call `sim_device health`: screen, reading and touch must match the task. View-only capture
can proceed without touch; do not describe it as successful interaction.

- Touch and element trees need WebDriverAgent. **Set up touch** in the viewer handles signing/setup. Changing teams
  or provisioning needs authorization. Until ready, snapshots may read pixels while actions are refused.
- Follow `note`, `text_reader` and `last_error`. Ask for the exact unlock, trust, permission or cable step; never
  enter a passcode. A network-visible phone can lack USB screen/input; not every failure is a cable fault.
- A slow tree may continue building after pixel fallback. Use returned text or screenshots meanwhile; do not poll
  snapshots in a tight loop. A child-limit warning means visible rows can be absent too.
- Session `restart` reconnects, never reboots, the phone. Use it for a diagnosed session problem, then check health
  once. An identical failure needs new evidence or an external change.
- Respect another agent's ownership; `steal` requires the person's request. Reading remains available. Input on the
  phone's own glass is not detected like viewer input: coordinate the run with the person.
- iOS can transform key-typed punctuation or refuse pasted Unicode. Inspect the value and tool note before retrying;
  use the on-screen keyboard where practical or report the limitation.
- Physical builds use project signing and lack simulator source stamps. Report provenance as unverified without
  a stamp. Letting the device go restores supported settings; never reset the phone.
