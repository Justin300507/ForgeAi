export default function MessageList({ messages }) {
  return (
    <div>
      {messages.map((msg, i) => (
        <div key={i}>{msg}</div>
      ))}
    </div>
  );
}