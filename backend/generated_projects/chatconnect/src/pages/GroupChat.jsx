import { useState } from 'react';
import MessageList from '../components/MessageList';
import MessageInput from '../components/MessageInput';
import GroupList from '../components/GroupList';

export default function GroupChat({ user }) {
  const [messages, setMessages] = useState([]);

  return (
    <div>
      <h1>Group Chat</h1>
      <GroupList />
      <MessageList messages={messages} />
      <MessageInput onSend={(msg) => setMessages([...messages, msg])} />
    </div>
  );
}