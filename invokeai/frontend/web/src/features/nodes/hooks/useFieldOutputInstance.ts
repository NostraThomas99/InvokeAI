import { createMemoizedSelector } from 'app/store/createMemoizedSelector';
import { stateSelector } from 'app/store/store';
import { useAppSelector } from 'app/store/storeHooks';
import { isInvocationNode } from 'features/nodes/types/invocation';
import { useMemo } from 'react';

export const useFieldOutputInstance = (nodeId: string, fieldName: string) => {
  const selector = useMemo(
    () =>
      createMemoizedSelector(stateSelector, ({ nodes }) => {
        const node = nodes.nodes.find((node) => node.id === nodeId);
        if (!isInvocationNode(node)) {
          return;
        }
        return node.data.outputs[fieldName];
      }),
    [fieldName, nodeId]
  );

  const fieldTemplate = useAppSelector(selector);

  return fieldTemplate;
};
