import { useState } from 'react';
import MessageList from '../components/MessageList';
import MessageInput from '../components/MessageInput';
import UserList from '../components/UserList';
import StatusIndicator from '../components/StatusIndicator';

export default function Chat({ user }) {
  const [messages, setMessages] = useState([]);

  return (
    <div>
      <h1>Chat</h1>
      <StatusIndicator online={true} />
      <UserList />
      <MessageList messages={messages} />
      <MessageInput onSend={(msg) => setMessages([...messages, msg])} />
    </div>
  );
}