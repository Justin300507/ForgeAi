import { useState } from 'react';
import MessageList from '../components/MessageList';
import InputBox from '../components/InputBox';
import GroupList from '../components/GroupList';

export default function GroupChat() {
  const [messages, setMessages] = useState([]);

  const handleSend = (message) => {
    setMessages([...messages, message]);
  };

  return (
    <div>
      <h1>Group Chat</h1>
      <GroupList />
      <MessageList messages={messages} />
      <InputBox onSend={handleSend} />
    </div>
  );
}