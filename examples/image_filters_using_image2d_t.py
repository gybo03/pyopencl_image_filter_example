#!/usr/bin/env python3
# this example shows how to blur, gray scale or brightness adjust the picture
# using pyopencl and  image2d_t buffer objects
# this python script will ask you for:
# input image path
# number of which filter you want to apply to
# if gaussian blur is chosen it will ask you for parameters of gaussian kernel
# if brightness adjustment is chosen it will ask you for a scaler that will
# determen how much to adjust it
# this script was my homework that was given in Parallel Algorithims
# for more info contact me on https://github.com/gybo03
import os
import sys
from PIL import Image
import numpy as np
import pyopencl as cl

platform = cl.get_platforms()[0]
device = platform.get_devices()[0]
context = cl.Context([device])
queue = cl.CommandQueue(context)
n_threads = device.max_work_group_size


def calc_kernel(size: int, sigma: float) -> np.ndarray:
    x = np.linspace(- (size // 2), size // 2, size)
    x /= np.sqrt(2)*sigma
    x2 = x**2
    kernel = np.exp(- x2[:, None] - x2[None, :])
    return kernel / kernel.sum()


def sum_arr(arr: np.array) -> int:
    # N is first bigger number that is multiple of n_threads
    n = (np.ceil(len(arr)/n_threads)*n_threads).astype(np.int32)
    # this fills the array with trailing zeros so that the kernel
    # doesnt access random memory
    arr = np.concatenate((arr, np.zeros(n-len(arr))))
    mf = cl.mem_flags
    # creates a openCL buffer obj that has the copy of an array
    arr_buf = cl.Buffer(context, mf.READ_ONLY |
                        mf.COPY_HOST_PTR, hostbuf=arr.astype(np.int32))
    # in reduce var will be sum(arr[0:n_threads]) for 0th index and so on
    reduce = np.empty(n//n_threads).astype(np.int32)
    # same as prev openCL buf this one will be filled by kernel
    reduce_buf = cl.Buffer(context, mf.READ_WRITE, size=reduce.nbytes)
    # in local buf, reduction is applied on local memory
    local_buf = cl.LocalMemory(4*n_threads)
    program.reduce_sum(queue, (n,), (n_threads,), arr_buf,
                       reduce_buf, local_buf).wait()
    cl.enqueue_copy(queue, reduce, reduce_buf)
    # this process could be applied more than once on an array but for
    # images applying reduction_sum once is more than enough
    return np.sum(reduce)


def sum_matrix(matrix: np.array) -> np.array:
    # this function calculates avg values for every pixel value
    # when you flatten a RGBA image every 4th index is red value
    # starting at 0. For blue value it is every 4th index but
    # starting from 1 and so on
    return np.array([sum_arr(np.array(matrix[::4])),
                     sum_arr(np.array(matrix[1::4])),
                     sum_arr(np.array(matrix[2::4]))])


program = cl.Program(context, """
__kernel void gray_scale(read_only const image2d_t src,
                                  write_only image2d_t dest,
                                   const sampler_t sampler){
  int2 pos = (int2)(get_global_id(0),get_global_id(1));
  uint4 pixel = read_imageui(src,sampler,pos);
  uint g=pixel.x*299/1000+pixel.y*578/1000+pixel.z*114/1000;
  write_imageui(dest,pos,(uint4)(g,g,g,255));
}
__kernel void brightness_adj(read_only const image2d_t src,
                                       write_only image2d_t dest,
                                       const sampler_t sampler,
                                       const float scalar,
                                       const float4 mean_intensity){
  int2 pos = (int2)(get_global_id(0),get_global_id(1));
  uint4 pixel = read_imageui(src,sampler,pos);
  uint4 new_pixel = convert_uint4((float4)scalar*(convert_float4(pixel)\
                                        -mean_intensity)+mean_intensity);
  uint4 overflow = convert_uint4(new_pixel>(uint4)255);
  write_imageui(dest,pos,select(new_pixel,(uint4)255,overflow));
}
__kernel void reduce_sum(__global int *in,
            __global int *reduce,
            __local int *buffer)
{
  uint gid = get_global_id(0);
  uint wid = get_group_id(0);
  uint lid = get_local_id(0);
  uint gs = get_local_size(0);
  buffer[lid] = in[gid];

  for(uint s = gs/2; s > 0; s >>= 1) {
    barrier(CLK_LOCAL_MEM_FENCE);
    if(lid < s) {
      buffer[lid] += buffer[lid+s];
    }
  }
  if(lid == 0) reduce[wid] = buffer[lid];
}
__kernel void gaussian_blur(read_only const image2d_t src,
                                       write_only image2d_t dest,
                                       const sampler_t sampler,
                                       read_only const image2d_t gauss_kernel,
                                       const short dim){
  int2 pos = (int2)(get_global_id(0),get_global_id(1));
  uint4 pixel=(uint4)0;
  float4 src_pixel,gauss_pixel;
  for(int i = -dim/2;i<dim/2;i++){
    for(int j = -dim/2;j<dim/2;j++){
      src_pixel=convert_float4(read_imageui(src,sampler,(int2)(i,j)+pos));
      gauss_pixel=convert_float4(read_imageui(gauss_kernel,sampler,(int2)(i+dim/2,j+dim/2)))/(float4)255;
      pixel=pixel+convert_uint4(src_pixel*gauss_pixel);
      if(get_global_id(0)==0&&get_global_id(1)==0){
        printf("i=%d,j=%d",i,j);
      }
    }
  }
  uint4 overflow = convert_uint4(pixel>(uint4)255);
  write_imageui(dest,pos,select(pixel,(uint4)255,overflow));
}
""").build()
image_path = input("Enter path to the image:")
try:
    # intel-compile-runtime supports RGBA so im using that format
    image = np.array(Image.open(image_path).convert("RGBA"))
except FileNotFoundError:
    print("Image does not exist")
    sys.exit()

src_buf = cl.image_from_array(context, image, 4)

image_format = cl.ImageFormat(
    cl.channel_order.RGBA, cl.channel_type.UNSIGNED_INT8)

shape = (image.shape[1], image.shape[0])

dest_buf = cl.create_image(context, cl.mem_flags.WRITE_ONLY,
                           image_format, shape)
sampler = cl.Sampler(
    context, False, cl.addressing_mode.CLAMP_TO_EDGE, cl.filter_mode.NEAREST)

print("Enter one of the next values\n")
i = int(input("Gray scale:1\nGaussian blur:2\nBrightenss adjustment:3\n"))

if i in range(1, 4):
    if i == 1:
        program.gray_scale(queue, shape, None, src_buf, dest_buf, sampler)
    elif i == 2:
        ker = calc_kernel(int(input("Enter kernel dimension:")),
                          int(input("Enter kernel sigma value:")))
        # line below creates a gauss kernel but stacks it in Z axis
        # so that it can be interpreted as an Image
        ker = np.array(np.stack((ker, ker, ker, np.ones((len(ker), len(ker)))), axis=2)
                       * 255).astype(np.uint8)

        gauss_buf = cl.image_from_array(context, ker, 4)

        program.gaussian_blur(queue, shape, None, src_buf,
                              dest_buf, sampler, gauss_buf, np.int32(len(ker)))
    elif i == 3:
        # turn the image into 1D array so that it can be sum reduced in opencl
        # it could have been 2D or even 3D but this was simpler
        temp_image = image.flatten().astype(np.int32)
        # concatenates [avg(red_pixel_val),avg(blue_pixel_val),avg(green_pixel_val)]
        # with 255 for the alpha value
        pixel = np.concatenate(((sum_matrix(temp_image) /
                                 (len(temp_image)//4)).astype(np.float32),
                                np.array([255.0]).astype(np.float32)))

        scale = input("Enter the scale of brightness adjustment:")
        program.brightness_adj(queue, shape, None, src_buf,
                               dest_buf, sampler, np.float32(scale), pixel)
    dest = np.empty_like(image)
    cl.enqueue_copy(queue, dest, dest_buf, origin=(0, 0), region=shape)
    file_name, file_extension = os.path.splitext(image_path)
    Image.fromarray(dest).save(file_name+"_edited"+file_extension, "PNG")
else:
    print("wrong value should be 1, 2 or 3")
