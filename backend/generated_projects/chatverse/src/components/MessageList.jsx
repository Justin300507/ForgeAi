export default function MessageList({ messages = [] }) {
  return (
    <div>
      {messages.map((msg, index) => (
        <div key={index}>{msg}</div>
      ))}
    </div>
  );
}