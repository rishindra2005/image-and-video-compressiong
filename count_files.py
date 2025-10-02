import os
from tqdm import tqdm

def count_files_by_extension(directory):
    extension_counts = {}
    file_list = os.listdir(directory)
    for filename in tqdm(file_list, desc="Counting files"):
        if os.path.isfile(os.path.join(directory, filename)):
            _, extension = os.path.splitext(filename)
            if extension:
                extension = extension.lower()
                extension_counts[extension] = extension_counts.get(extension, 0) + 1
    return extension_counts

def main():
    camera_dir = '/home/rishi/Desktop/mummy/Camera'
    if os.path.exists(camera_dir):
        extension_counts = count_files_by_extension(camera_dir)
        for ext, count in extension_counts.items():
            print(f"{ext} - {count}")
    else:
        print(f"Directory not found: {camera_dir}")

if __name__ == "__main__":
    main()