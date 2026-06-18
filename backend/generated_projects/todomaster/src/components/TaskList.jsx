import React from 'react';
import TaskCard from './TaskCard';

export default function TaskList({ tasks }) {
  return (
    <div>
      {tasks.map((task) => (
        <TaskCard key={task.id} task={task} />
      ))}
    </div>
  );
}