import React, { useState } from 'react';
import DueDatePicker from './DueDatePicker';

export default function TaskForm({ onSubmit }) {
  const [title, setTitle] = useState('');
  const [priority, setPriority] = useState(1);

  const handleSubmit = (e) => {
    e.preventDefault();
    onSubmit({ title, priority });
    setTitle('');
    setPriority(1);
  };

  return (
    <form onSubmit={handleSubmit}>
      <input
        type="text"
        value={title}
        onChange={(e) => setTitle(e.target.value)}
        placeholder="Task title"
        required
      />
      <select
        value={priority}
        onChange={(e) => setPriority(Number(e.target.value))}
      >
        <option value="1">Low</option>
        <option value="2">Medium</option>
        <option value="3">High</option>
      </select>
      <DueDatePicker />
      <button type="submit">Add Task</button>
    </form>
  );
}