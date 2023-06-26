import { Flex } from '@chakra-ui/react';
import { RootState } from 'app/store/store';
import { useAppSelector } from 'app/store/storeHooks';

import { useListModelsQuery } from 'services/api/endpoints/models';
import CheckpointModelEdit from './ModelManagerPanel/CheckpointModelEdit';
import DiffusersModelEdit from './ModelManagerPanel/DiffusersModelEdit';
import ModelList from './ModelManagerPanel/ModelList';

export default function ModelManagerPanel() {
  const { data: pipelineModels } = useListModelsQuery({
    model_type: 'pipeline',
  });

  const openModel = useAppSelector(
    (state: RootState) => state.system.openModel
  );

  const renderModelEditTabs = () => {
    if (!openModel || !pipelineModels) return;

    if (pipelineModels['entities'][openModel]['model_format'] === 'diffusers') {
      return (
        <DiffusersModelEdit
          modelToEdit={openModel}
          retrievedModel={pipelineModels['entities'][openModel]}
        />
      );
    } else {
      return (
        <CheckpointModelEdit
          modelToEdit={openModel}
          retrievedModel={pipelineModels['entities'][openModel]}
        />
      );
    }
  };
  return (
    <Flex width="100%" columnGap={8}>
      <ModelList />
      {renderModelEditTabs()}
    </Flex>
  );
}
